"""The gateway: the one door employees use.

An employee's request arrives here with an identity from SSO (the `--user` flag stands in for it
locally). The gateway checks the allowlist and policy on the usage-logs branch, hands the run to a
backend, checks every step report against policy, and appends one run record and any audit events
to the ledger. Employees never get credentials for the repo or the Claude API.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .backends import RunContext, get_backend
from .governance import Governance, Violation
from .inputs import declared_size, summarize
from .gitutil import rev_parse
from .ledger import BOT_EMAIL, Ledger
from .orchestrator import file_digest
from .pricing import add_tokens
from .registry import Registry

RUN_SCHEMA = "gatehouse.run/v1"


class Gateway:
    def __init__(self, repo: Path, ledger: Ledger | None = None, work_dir: Path | None = None):
        self.repo = Path(repo)
        self.ledger = ledger or Ledger(self.repo)
        self.work_dir = Path(work_dir or self.repo / ".gatehouse" / "runs")
        self._registry: Registry | None = None
        self._registry_commit: str | None = None

    def registry(self) -> Registry:
        """Definitions and versions as of the current commit; reloaded whenever main moves."""
        commit = rev_parse(self.repo, "HEAD")
        if self._registry is None or commit != self._registry_commit:
            self._registry, self._registry_commit = Registry(self.repo), commit
        return self._registry

    def _runs_today(self, user: str, day: str) -> int:
        return sum(
            1
            for r in self.ledger.runs()
            if r["user"] == user and r["started_at"][:10] == day and r["status"] not in ("denied", "pending_approval")
        )

    def run(
        self,
        user: str,
        pipeline: str,
        input_path: str | Path,
        backend: str = "dry-run",
        approval_ticket: str | None = None,
        inject: dict[str, str] | None = None,
        now: datetime | None = None,
        run_id: str | None = None,
        origin: str = "gateway",
    ) -> dict:
        user = user.lower()
        registry = self.registry()
        gov = Governance.from_ledger(self.ledger)
        pipe = registry.pipelines[pipeline]
        started = (now or datetime.now(timezone.utc)).replace(microsecond=0)
        run_id = run_id or f"run_{started:%Y%m%d}_{secrets.token_hex(4)}"

        input_path = Path(input_path)
        raw = input_path.read_text()
        deal = json.loads(raw)
        try:
            ref = str(input_path.resolve().relative_to(self.repo.resolve()))
        except ValueError:
            ref = input_path.name
        input_meta = {
            "ref": ref,
            "sha256": "sha256:" + hashlib.sha256(raw.encode()).hexdigest(),
            "bytes": len(raw.encode()),
            "summary": summarize(deal),
        }

        decision = gov.authorize(
            user,
            pipe.name,
            pipe.max_cost_usd,
            {"deal_size_usd": declared_size(deal)},
            raw,
            self._runs_today(user, started.date().isoformat()),
            approval_ticket,
        )
        orch = registry.orchestrator
        record = {
            "schema": RUN_SCHEMA,
            "run_id": run_id,
            "pipeline": pipe.name,
            "user": user,
            "groups": decision.groups,
            "surface": backend,
            "simulated": backend == "dry-run",
            "origin": origin,
            "started_at": started.isoformat(),
            "repo_commit": registry.repo_commit(),
            "orchestrator": {
                "agent": orch.name,
                "agent_file": orch.path,
                "model": orch.model,
                **registry.skill_version(orch.skills[0]).as_log(),
            },
            "input": input_meta,
        }
        audit: list[dict] = []
        stamp = started.isoformat()

        if not decision.allowed:
            record.update(
                status="denied" if decision.status == "denied" else "pending_approval",
                ended_at=stamp,
                duration_ms=0,
                steps=[],
                tokens={},
                cost_usd=0.0,
                output=None,
                errors=[{"step": 0, "code": v.code, "message": v.message} for v in decision.violations],
                policy=decision.as_dict(),
                session_id=None,
            )
            for v in decision.violations:
                audit.append({"type": "policy.violation", "at": stamp, "actor": user, "run_id": run_id, "detail": v.as_dict()})
            audit.append(
                {"type": "run.denied" if decision.status == "denied" else "run.held_for_approval", "at": stamp,
                 "actor": user, "run_id": run_id, "detail": {"pipeline": pipe.name}}
            )
            self.ledger.append(runs=[record], audit=audit, message=f"run {run_id}: {record['status']}", when=started)
            return record

        if approval_ticket:
            audit.append({"type": "approval.used", "at": stamp, "actor": user, "run_id": run_id,
                          "detail": {"ticket": approval_ticket, "pipeline": pipe.name}})

        ctx = RunContext(
            run_id=run_id,
            registry=registry,
            pipeline=pipe,
            input_path=str(input_path),
            started_at=started,
            budget_usd=decision.max_cost_usd,
            run_dir=self.work_dir / run_id,
            inject=inject or {},
        )
        execd = get_backend(backend).execute(ctx)
        res = execd.result

        violations = [v for s in res.steps for v in gov.step_violations(s)]
        if res.status == "budget_exceeded":
            violations.append(
                Violation("budget.reached", "medium", f"run stopped at ${decision.max_cost_usd:.2f}", "budgets.default_max_cost_usd")
            )
        policy = decision.as_dict()
        policy["violations"] = [v.as_dict() for v in violations]
        for v in policy["violations"]:
            audit.append({"type": "policy.violation", "at": stamp, "actor": user, "run_id": run_id, "detail": v})

        memo = res.artifacts.get("memo")
        overhead_ms = 1500 + 900 * len(res.steps)  # session start + orchestrator turns
        duration = res.elapsed_ms + overhead_ms
        cost = round(res.cost_usd + execd.orchestrator_cost_usd, 6)
        record.update(
            status=res.status,
            ended_at=(started + timedelta(milliseconds=duration)).isoformat(timespec="seconds"),
            duration_ms=duration,
            steps=res.steps,
            orchestrator_usage={"tokens": execd.orchestrator_tokens, "cost_usd": execd.orchestrator_cost_usd},
            tokens=add_tokens(res.tokens, execd.orchestrator_tokens),
            cost_usd=execd.platform_list_cost_usd if execd.platform_list_cost_usd is not None else cost,
            output={
                "summary": res.steps[-1]["summary"] if res.steps else None,
                "recommendation": _recommendation(memo),
                "artifact": "memo.md" if memo else None,
                "sha256": file_digest(memo) if memo else None,
            },
            errors=res.errors,
            policy=policy,
            session_id=execd.session_id,
        )
        self.ledger.append(runs=[record], audit=audit, message=f"run {run_id}: {res.status}", when=started)
        return record

    # ---------- admin ----------

    def change_allowlist(self, actor: str, user: str, group: str, grant: bool, now: datetime | None = None) -> dict:
        import yaml

        gov = Governance.from_ledger(self.ledger)
        if not gov.is_admin(actor):
            raise PermissionError(f"{actor} is not an admin")
        if group not in (gov.allowlist.get("groups") or {}):
            raise ValueError(f"unknown group {group!r}")
        allow = gov.allowlist
        users = allow.setdefault("users", {})
        groups = set(users.get(user.lower(), []))
        before = sorted(groups)
        groups.add(group) if grant else groups.discard(group)
        if groups:
            users[user.lower()] = sorted(groups)
        else:
            users.pop(user.lower(), None)
        when = now or datetime.now(timezone.utc)
        event = {
            "type": "governance.changed",
            "at": when.isoformat(timespec="seconds"),
            "actor": actor.lower(),
            "detail": {"file": "governance/allowlist.yaml", "user": user.lower(), "before": before, "after": sorted(groups)},
        }
        text = "# Managed by `gatehouse grant` / `gatehouse revoke`. Every change has an audit event.\n" + yaml.safe_dump(
            allow, sort_keys=False
        )
        self.ledger.append(audit=[event], replace={"governance/allowlist.yaml": text},
                           message=f"allowlist: {'grant' if grant else 'revoke'} {group} for {user}", when=when)
        return event


def _recommendation(memo_path: str | None) -> str | None:
    if not memo_path or not Path(memo_path).exists():
        return None
    for line in Path(memo_path).read_text().splitlines():
        if line.startswith("**") and "." in line:
            return line.strip("*").split(".")[0].strip("*")
    return None


__all__ = ["Gateway", "BOT_EMAIL"]
