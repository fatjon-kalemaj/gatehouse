"""Reference implementation of the deal-desk-orchestrator skill's procedure.

In the cloud, the orchestrator agent follows skills/deal-desk-orchestrator/SKILL.md inside a Managed
Agents session. Locally (dry run and tests), this class follows the same procedure in code against
any `Worker`: one step at a time, validate each report, one retry on an invalid report, stop on
failure, stop at the budget, and treat the gate step's `needs_review` as the run's outcome.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from .pricing import add_tokens, cost_usd
from .registry import AgentSpec, Pipeline, Registry
from .schema import validate_step_result


@dataclass
class Brief:
    run_id: str
    step_index: int
    agent: str
    consumes: dict[str, str]  # artifact name -> path
    produces: str
    produces_path: str
    follow_up: str | None = None  # set on the retry after an invalid report


@dataclass
class WorkerResult:
    report: object  # the parsed report; anything that is not a dict is invalid
    tokens: dict
    duration_ms: int
    error: str | None = None  # the worker crashed or the platform returned an error


Worker = Callable[[AgentSpec, Brief], WorkerResult]


@dataclass
class OrchestratorResult:
    status: str  # succeeded | needs_review | failed | budget_exceeded
    steps: list[dict]
    errors: list[dict] = field(default_factory=list)
    artifacts: dict[str, str] = field(default_factory=dict)
    elapsed_ms: int = 0

    @property
    def tokens(self) -> dict:
        total: dict = {}
        for s in self.steps:
            total = add_tokens(total, s["tokens"])
        return total

    @property
    def cost_usd(self) -> float:
        return round(sum(s["cost_usd"] for s in self.steps), 6)


def file_digest(path: str | Path) -> str | None:
    p = Path(path)
    if not p.exists():
        return None
    return "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest()


class SequentialOrchestrator:
    MAX_ATTEMPTS = 2

    def __init__(self, registry: Registry, pipeline: Pipeline, worker: Worker, run_dir: Path, budget_usd: float):
        self.registry = registry
        self.pipeline = pipeline
        self.worker = worker
        self.run_dir = Path(run_dir)
        self.budget_usd = budget_usd

    def run(self, run_id: str, input_path: str, started_at: datetime) -> OrchestratorResult:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        artifacts = {"input": str(input_path)}
        result = OrchestratorResult(status="succeeded", steps=[], artifacts=artifacts)
        elapsed = 0

        for index, step in enumerate(self.pipeline.steps, start=1):
            # Same rule as a Managed Agents session budget: checked before new work starts.
            if result.cost_usd >= self.budget_usd:
                result.status = "budget_exceeded"
                result.errors.append(
                    {"step": index, "code": "budget.reached", "message": f"run reached ${self.budget_usd:.2f} before step {index}"}
                )
                break

            agent = self.registry.agents[step.agent]
            ext = "md" if step.produces == "memo" else "json"
            brief = Brief(
                run_id=run_id,
                step_index=index,
                agent=agent.name,
                consumes={name: artifacts[name] for name in step.consumes},
                produces=step.produces,
                produces_path=str(self.run_dir / f"{step.produces}.{ext}"),
            )

            tokens: dict = {}
            duration = 0
            attempts = 0
            report: object = None
            problems: list[str] = []
            error: str | None = None
            while attempts < self.MAX_ATTEMPTS:
                attempts += 1
                res = self.worker(agent, brief)
                tokens = add_tokens(tokens, res.tokens)
                duration += res.duration_ms
                report, error = res.report, res.error
                if error:
                    break
                problems = validate_step_result(report, agent.name)
                if not problems and not Path(report["output"]["artifact"]).exists():
                    problems = [f"artifact {report['output']['artifact']} was not written"]
                if not problems:
                    break
                brief.follow_up = "Your report was invalid: " + "; ".join(problems)

            skill = self.registry.skill_version(agent.skills[0]) if agent.skills else None
            record = {
                "index": index,
                "agent": agent.name,
                "agent_file": agent.path,
                "model": agent.model,
                "effort": agent.effort,
                **(skill.as_log() if skill else {}),
                "started_at": (started_at + timedelta(milliseconds=elapsed)).isoformat(timespec="seconds"),
                "duration_ms": duration,
                "attempts": attempts,
                "tokens": tokens,
                "cost_usd": cost_usd(agent.model, tokens),
            }
            elapsed += duration

            if error:
                record.update(status="failed", summary=error, issues=[], validation_errors=[])
                result.steps.append(record)
                result.status = "failed"
                result.errors.append({"step": index, "code": "agent.error", "message": error})
                break
            if problems:
                record.update(status="failed", summary="invalid step report", issues=[], validation_errors=problems)
                result.steps.append(record)
                result.status = "failed"
                result.errors.append({"step": index, "code": "schema.invalid", "message": "; ".join(problems)})
                break

            artifact = report["output"]["artifact"]
            record.update(
                status=report["status"],
                summary=report["summary"],
                issues=report["issues"],
                confidence=report.get("confidence"),
                artifact=Path(artifact).name,
                output_digest=file_digest(artifact),
                validation_errors=[],
            )
            result.steps.append(record)
            artifacts[step.produces] = artifact

            if report["status"] == "failed":
                result.status = "failed"
                codes = ", ".join(i["code"] for i in report["issues"]) or "no issue codes"
                result.errors.append({"step": index, "code": "step.failed", "message": f"{agent.name} failed: {codes}"})
                break
            if step.gate and report["status"] == "needs_review":
                result.status = "needs_review"

        result.elapsed_ms = elapsed
        return result
