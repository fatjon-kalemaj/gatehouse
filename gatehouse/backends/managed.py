"""Cloud backend: runs the orchestrator as a Claude Managed Agents session.

Setup happens once, outside the request path: `ant apply agents environments` syncs the agent and
environment files and records their IDs in claude-lock.json. This backend only creates sessions.

Per run it:
  1. creates a session of the orchestrator agent, with a hard list-cost budget equal to the run cap,
     and mounts this repo read-only at the exact commit the gateway logged (the token goes to
     Anthropic's git proxy and never enters the sandbox);
  2. sends the kickoff message and reads the event stream until the session goes idle;
  3. turns each worker's report (`agent.thread_message_received`) into a step record, and takes the
     run cost from the session's own `usage.list_cost`.

Written against the documented Managed Agents API (beta header `managed-agents-2026-04-01`, set by the
SDK). It is not exercised by the offline tests, which use the dry-run backend.
"""

from __future__ import annotations

import json
import os
import time
from datetime import timedelta

from ..orchestrator import OrchestratorResult
from ..pricing import add_tokens, cost_usd
from ..schema import validate_step_result
from . import ExecResult, RunContext

MOUNT = "/workspace/deal-desk"


def _text(blocks) -> str:
    return "".join(getattr(b, "text", "") for b in blocks or [])


def _json_object(text: str) -> object:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        return text
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return text


class ManagedAgentsBackend:
    name = "managed"

    def __init__(self, client, agent_id: str, environment_id: str, repo_url: str, repo_read_token: str):
        self.client = client
        self.agent_id = agent_id
        self.environment_id = environment_id
        self.repo_url = repo_url
        self.repo_read_token = repo_read_token

    @classmethod
    def from_env(cls) -> "ManagedAgentsBackend":
        import anthropic  # optional dependency: pip install 'gatehouse[cloud]'

        missing = [
            k
            for k in ("GATEHOUSE_ORCHESTRATOR_AGENT_ID", "GATEHOUSE_ENVIRONMENT_ID", "GATEHOUSE_REPO_URL", "GATEHOUSE_REPO_READ_TOKEN")
            if not os.environ.get(k)
        ]
        if missing:
            raise RuntimeError(f"managed backend needs {', '.join(missing)} (see README, 'Running in the cloud')")
        return cls(
            anthropic.Anthropic(),  # API key, `ant auth login` profile, or workload identity federation
            os.environ["GATEHOUSE_ORCHESTRATOR_AGENT_ID"],
            os.environ["GATEHOUSE_ENVIRONMENT_ID"],
            os.environ["GATEHOUSE_REPO_URL"],
            os.environ["GATEHOUSE_REPO_READ_TOKEN"],
        )

    def execute(self, ctx: RunContext) -> ExecResult:
        reg = ctx.registry
        session = self.client.beta.sessions.create(
            agent=self.agent_id,
            environment_id=self.environment_id,
            title=f"{ctx.pipeline.name} {ctx.run_id}",
            budget={"type": "limit", "max_list_cost": {"amount": str(int(round(ctx.budget_usd * 100))), "currency": "USD"}},
            resources=[
                {
                    "type": "github_repository",
                    "url": self.repo_url,
                    "authorization_token": self.repo_read_token,  # fine-grained, Contents: Read only
                    "mount_path": MOUNT,
                    "checkout": {"type": "commit", "sha": reg.repo_commit()},
                }
            ],
        )
        with open(ctx.input_path) as fh:
            deal = json.load(fh)
        kickoff = json.dumps(
            {
                "run_id": ctx.run_id,
                "pipeline": ctx.pipeline.name,
                "input": deal,
                "max_cost_usd": ctx.budget_usd,
            },
            indent=2,
        )

        sent_at: dict[str, float] = {}
        reports: list[tuple[str, object, int]] = []
        orch_tokens: dict = {}
        list_cost: float | None = None
        stop_reason = None
        deadline = time.monotonic() + ctx.pipeline.max_duration_s

        with self.client.beta.sessions.events.stream(session_id=session.id) as stream:
            self.client.beta.sessions.events.send(
                session_id=session.id,
                events=[{"type": "user.message", "content": [{"type": "text", "text": kickoff}]}],
            )
            for event in stream:
                if time.monotonic() > deadline:
                    self.client.beta.sessions.events.send(session_id=session.id, events=[{"type": "user.interrupt"}])
                    stop_reason = "deadline"
                    break
                t = event.type
                if t == "agent.thread_message_sent":
                    sent_at.setdefault(event.to_agent_name, time.monotonic())
                elif t == "agent.thread_message_received":
                    name = event.from_agent_name
                    took = int((time.monotonic() - sent_at.pop(name, time.monotonic())) * 1000)
                    reports.append((name, _json_object(_text(event.content)), took))
                elif t == "span.model_request_end" and getattr(event, "model_usage", None):
                    u = event.model_usage
                    orch_tokens = add_tokens(
                        orch_tokens,
                        {
                            "input": u.input_tokens,
                            "output": u.output_tokens,
                            "cache_read": u.cache_read_input_tokens,
                            "cache_write": u.cache_creation_input_tokens,
                        },
                    )
                elif t in ("agent.tool_use", "agent.mcp_tool_use") and getattr(event, "evaluated_permission", None) == "ask":
                    # Nobody is watching a gateway session: anything the auto policy cannot decide is denied.
                    self.client.beta.sessions.events.send(
                        session_id=session.id,
                        events=[{"type": "user.tool_confirmation", "tool_use_id": event.id, "result": "deny"}],
                    )
                elif t == "session.usage":
                    list_cost = int(event.list_cost.amount) / 100 if event.list_cost else None
                elif t == "session.status_idle":
                    stop_reason = getattr(event.stop_reason, "type", event.stop_reason)
                    if stop_reason != "requires_action":
                        break
                elif t == "session.status_terminated":
                    stop_reason = "terminated"
                    break

        session = self.client.beta.sessions.retrieve(session.id)
        usage = getattr(session, "usage", None)
        if usage is not None and getattr(usage, "list_cost", None):
            list_cost = int(usage.list_cost.amount) / 100

        return ExecResult(
            result=self._to_result(ctx, reports, stop_reason),
            orchestrator_tokens=orch_tokens,
            orchestrator_cost_usd=cost_usd(reg.orchestrator.model, orch_tokens),
            session_id=session.id,
            platform_list_cost_usd=list_cost,
        )

    def _to_result(self, ctx: RunContext, reports, stop_reason) -> OrchestratorResult:
        reg = ctx.registry
        steps, errors = [], []
        status = "succeeded"
        elapsed = 0
        by_agent = {}
        for name, report, took in reports:
            by_agent.setdefault(name, []).append((report, took))
        for index, step in enumerate(ctx.pipeline.steps, 1):
            if step.agent not in by_agent:
                break
            agent = reg.agents[step.agent]
            report, took = by_agent[step.agent][-1]  # the orchestrator may have asked once for a corrected report
            problems = validate_step_result(report, agent.name)
            rec = {
                "index": index,
                "agent": agent.name,
                "agent_file": agent.path,
                "model": agent.model,
                "effort": agent.effort,
                **reg.skill_version(agent.skills[0]).as_log(),
                "started_at": (ctx.started_at + timedelta(milliseconds=elapsed)).isoformat(timespec="seconds"),
                "duration_ms": sum(t for _, t in by_agent[step.agent]),
                "attempts": len(by_agent[step.agent]),
                "tokens": {},  # per-thread usage is in the Console session viewer; the run total is authoritative
                "cost_usd": 0.0,
                "validation_errors": problems,
            }
            elapsed += rec["duration_ms"]
            if problems:
                rec.update(status="failed", summary="invalid step report", issues=[])
                steps.append(rec)
                errors.append({"step": index, "code": "schema.invalid", "message": "; ".join(problems)})
                status = "failed"
                break
            rec.update(status=report["status"], summary=report["summary"], issues=report["issues"],
                       confidence=report.get("confidence"), artifact=report["output"]["artifact"])
            steps.append(rec)
            if report["status"] == "failed":
                status = "failed"
                errors.append({"step": index, "code": "step.failed", "message": report["summary"]})
                break
            if step.gate and report["status"] == "needs_review":
                status = "needs_review"
        if stop_reason == "budget_reached":
            status = "budget_exceeded"
            errors.append({"step": len(steps) + 1, "code": "budget.reached", "message": "session paused at its budget"})
        elif stop_reason in ("deadline", "terminated") or (status == "succeeded" and len(steps) < len(ctx.pipeline.steps)):
            status = "failed"
            errors.append({"step": len(steps) + 1, "code": f"session.{stop_reason or 'incomplete'}", "message": "pipeline did not finish"})
        return OrchestratorResult(status=status, steps=steps, errors=errors, elapsed_ms=elapsed)
