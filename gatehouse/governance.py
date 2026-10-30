"""Allowlist and policy checks. Both files are read from the usage-logs branch, never from main."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import yaml

from .ledger import Ledger

SEVERITY_FOR_ACTION = {"block": "high", "flag": "medium"}


@dataclass
class Violation:
    code: str
    severity: str
    message: str
    rule: str

    def as_dict(self) -> dict:
        return {"code": self.code, "severity": self.severity, "message": self.message, "rule": self.rule}


@dataclass
class Decision:
    status: str  # "allow" | "denied" | "pending_approval"
    groups: list[str]
    max_cost_usd: float
    violations: list[Violation] = field(default_factory=list)
    approval_ticket: str | None = None

    @property
    def allowed(self) -> bool:
        return self.status == "allow"

    def as_dict(self) -> dict:
        return {
            "decision": self.status,
            "groups": self.groups,
            "max_cost_usd": self.max_cost_usd,
            "approval_ticket": self.approval_ticket,
            "violations": [v.as_dict() for v in self.violations],
        }


class Governance:
    def __init__(self, allowlist: dict, policy: dict):
        self.allowlist = allowlist
        self.policy = policy

    @classmethod
    def from_ledger(cls, ledger: Ledger) -> "Governance":
        allow = ledger.read("governance/allowlist.yaml")
        pol = ledger.read("governance/policy.yaml")
        if allow is None or pol is None:
            raise RuntimeError("governance files missing on the usage-logs branch; run `gatehouse ledger init`")
        return cls(yaml.safe_load(allow), yaml.safe_load(pol))

    # ---------- identity ----------

    def groups_for(self, user: str) -> list[str]:
        return list((self.allowlist.get("users") or {}).get(user.lower(), []))

    def is_admin(self, user: str) -> bool:
        return user.lower() in [a.lower() for a in self.allowlist.get("admins", [])]

    def can_run(self, user: str, pipeline: str) -> bool:
        groups = self.allowlist.get("groups") or {}
        for g in self.groups_for(user):
            allowed = (groups.get(g) or {}).get("pipelines", [])
            if "*" in allowed or pipeline in allowed:
                return True
        return False

    def daily_limit(self, user: str) -> int:
        groups = self.allowlist.get("groups") or {}
        limits = [(groups.get(g) or {}).get("daily_run_limit", 0) for g in self.groups_for(user)]
        return max(limits, default=0)

    # ---------- the pre-run decision ----------

    def screen(self, text: str) -> list[Violation]:
        found = []
        for rule in self.policy.get("input_screening", []):
            hit = None
            if "pattern" in rule:
                m = re.search(rule["pattern"], text)
                hit = m.group(0) if m else None
            for term in rule.get("terms", []):
                if term.lower() in text.lower():
                    hit = term
            if hit:
                # Never echo the matched value itself into the log.
                found.append(
                    Violation(
                        code=rule["code"],
                        severity=SEVERITY_FOR_ACTION.get(rule.get("action", "block"), "high"),
                        message=f"{rule.get('reason', 'Input screening rule matched')} ({len(hit)} chars redacted)",
                        rule=f"input_screening.{rule['code']}",
                    )
                )
        return found

    def authorize(
        self,
        user: str,
        pipeline: str,
        pipeline_max_cost: float,
        deal: dict,
        input_text: str,
        runs_today: int,
        approval_ticket: str | None = None,
    ) -> Decision:
        groups = self.groups_for(user)
        cap = min(pipeline_max_cost, float((self.policy.get("budgets") or {}).get("default_max_cost_usd", pipeline_max_cost)))
        decision = Decision(status="allow", groups=groups, max_cost_usd=cap, approval_ticket=approval_ticket)

        if not groups:
            decision.violations.append(
                Violation("access.not_allowlisted", "high", f"{user} is not on the allowlist", "allowlist.users")
            )
        elif not self.can_run(user, pipeline):
            decision.violations.append(
                Violation("access.pipeline_not_granted", "high", f"groups {groups} may not run {pipeline}", "allowlist.groups")
            )
        elif runs_today >= self.daily_limit(user):
            decision.violations.append(
                Violation(
                    "quota.daily_runs", "medium", f"daily limit {self.daily_limit(user)} reached", "allowlist.groups.daily_run_limit"
                )
            )

        decision.violations.extend(self.screen(input_text))
        if decision.violations:
            decision.status = "denied"
            return decision

        for rule in self.policy.get("approvals", []):
            if rule.get("pipeline") not in (pipeline, "*"):
                continue
            threshold = (rule.get("when") or {}).get("deal_size_usd_gt")
            size = deal.get("deal_size_usd") or 0
            if threshold is not None and size > threshold and not approval_ticket:
                decision.status = "pending_approval"
                decision.violations.append(
                    Violation("approval.required", "medium", rule.get("reason", "approval required"), f"approvals.{rule['id']}")
                )
        return decision

    def step_violations(self, step_report: dict) -> list[Violation]:
        table = self.policy.get("step_issues") or {}
        out = []
        for issue in step_report.get("issues", []):
            if issue.get("code") in table:
                out.append(
                    Violation(issue["code"], table[issue["code"]], issue.get("message", ""), f"step_issues.{issue['code']}")
                )
        return out
