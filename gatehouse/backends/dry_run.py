"""Offline workers for the dry run. No API calls, no network.

Each worker does a deterministic, rule-based version of its skill so the artifacts are real files the
next step can read, and the QA gate makes real checks. Token counts are simulated from the text sizes
an actual session would send and receive (system prompt, skill, brief, consumed artifacts), so cost and
duration in the logs have realistic shape. Every dry-run record is marked `simulated: true`.
"""

from __future__ import annotations

import json
import random
import re
from pathlib import Path

from ..inputs import declared_size
from ..orchestrator import Brief, WorkerResult
from ..registry import AgentSpec, Registry

EFFORT_THINKING = {"low": 1.3, "medium": 2.0, "high": 3.0, "xhigh": 4.0, "max": 5.0}
EFFORT_TURNS = {"low": (2, 3), "medium": (3, 5), "high": (4, 6), "xhigh": (5, 8), "max": (6, 9)}

ASSET_CLASSES = {"private_equity", "private_credit", "real_estate", "infrastructure", "secondaries", "other"}


def fmt_usd(v: float | None) -> str:
    if v is None:
        return "not provided"
    if v >= 1e9:
        return f"${v / 1e9:.2f}B"
    return f"${v / 1e6:.1f}M"


def _report(agent: str, status: str, summary: str, artifact: str, issues=None, confidence=0.8, **extra) -> dict:
    return {
        "agent": agent,
        "status": status,
        "summary": summary,
        "output": {"artifact": artifact, **extra},
        "issues": issues or [],
        "confidence": confidence,
    }


class DryRunWorkers:
    """Callable worker: `workers(agent, brief) -> WorkerResult`."""

    def __init__(self, registry: Registry, run_seed: str, inject: dict[str, str] | None = None):
        self.registry = registry
        self.rng = random.Random(run_seed)
        self.inject = inject or {}  # agent name -> "error" | "invalid_report" | "figure_mismatch" | "expensive"
        self.reference = json.loads((registry.repo / "examples/reference/sector-notes.json").read_text())

    # ---------- token and time simulation ----------

    def _usage(self, agent: AgentSpec, brief: Brief, prompt_extra: str, output_text: str) -> tuple[dict, int]:
        stable = len(agent.system) + sum(len(self.registry.skill_text(s)) for s in agent.skills) + 9000  # + tool defs
        variable = len(json.dumps(brief.__dict__)) + len(prompt_extra)
        lo, hi = EFFORT_TURNS[agent.effort]
        turns = self.rng.randint(lo, hi)
        if self.inject.get(agent.name) == "expensive":
            turns *= 40
        out = int(len(output_text) / 3.6 * EFFORT_THINKING[agent.effort] * self.rng.uniform(0.85, 1.25))
        tokens = {
            "cache_write": stable // 4,
            "cache_read": (stable // 4) * (turns - 1),
            "input": (variable // 4) * turns + out // 2 * (turns - 1),
            "output": out,
        }
        duration_ms = int(turns * self.rng.uniform(1800, 3200) + out / self.rng.uniform(55, 80) * 1000)
        return tokens, duration_ms

    def __call__(self, agent: AgentSpec, brief: Brief) -> WorkerResult:
        kind = self.inject.get(agent.name)
        if kind == "error" and not brief.follow_up:
            tokens, ms = self._usage(agent, brief, "", "x" * 400)
            return WorkerResult(report=None, tokens=tokens, duration_ms=ms, error="worker thread terminated: upstream overloaded (529)")
        handler = getattr(self, "_" + agent.name.replace("-", "_"))
        consumed = {k: Path(v).read_text() for k, v in brief.consumes.items()}
        report, artifact_text = handler(brief, consumed)
        if kind == "invalid_report":
            report = {"agent": agent.name, "result": "done"}  # wrong shape both times
        tokens, ms = self._usage(agent, brief, "".join(consumed.values()), artifact_text + json.dumps(report))
        return WorkerResult(report=report, tokens=tokens, duration_ms=ms)

    # ---------- the four workers ----------

    def _deal_intake(self, brief: Brief, consumed: dict) -> tuple[dict, str]:
        deal = json.loads(consumed["input"])
        f = deal.get("fields", {})
        size = declared_size(deal)
        record = {
            "deal_name": deal.get("deal_name"),
            "asset_class": f.get("asset_class") if f.get("asset_class") in ASSET_CLASSES else None,
            "strategy": f.get("strategy"),
            "sector": f.get("sector"),
            "geography": f.get("geography"),
            "deal_size_usd": size,
            "check_size_usd": f.get("check_size_usd"),
            "key_metrics": f.get("key_metrics", {}),
            "sponsor": f.get("sponsor"),
            "deadline": f.get("deadline"),
        }
        record["missing_fields"] = [k for k, v in record.items() if v in (None, {}, "")]
        text = json.dumps(record, indent=2)
        Path(brief.produces_path).write_text(text)
        core_missing = [k for k in ("deal_size_usd", "asset_class", "sector") if record[k] is None]
        status = "needs_review" if core_missing else "ok"
        summary = f"Normalized {record['deal_name']}: {record['asset_class'] or 'unknown asset class'}, {record['sector'] or 'unknown sector'}, {fmt_usd(size)}."
        issues = [{"code": "field.missing", "severity": "medium", "message": f"{k} not in input"} for k in core_missing]
        return _report("deal-intake", status, summary, brief.produces_path, issues, 0.9 if status == "ok" else 0.6), text

    def _deal_research(self, brief: Brief, consumed: dict) -> tuple[dict, str]:
        intake = json.loads(consumed["intake"])
        ref = self.reference.get(intake.get("sector") or "", {})
        src = f"examples/reference/sector-notes.json#{intake.get('sector')}"
        pack = {
            "sector_view": ref.get("view", "No reference notes for this sector."),
            "comparables": [{**c, "source": src} for c in ref.get("comparables", [])],
            "risks": [{**r, "source": src} for r in ref.get("risks", [])],
            "diligence_questions": [f"Provide {m.replace('_', ' ')}." for m in intake.get("missing_fields", [])]
            + ref.get("questions", []),
            "sources": [src] if ref else [],
        }
        text = json.dumps(pack, indent=2)
        Path(brief.produces_path).write_text(text)
        ok = len(pack["comparables"]) >= 2 and len(pack["risks"]) >= 3
        summary = f"{len(pack['comparables'])} comparables, {len(pack['risks'])} risks, {len(pack['diligence_questions'])} open questions; web tools off, reference notes only."
        issues = [] if ok else [{"code": "research.thin", "severity": "medium", "message": "fewer than 2 sourced comparables or 3 sourced risks"}]
        return _report("deal-research", "ok" if ok else "needs_review", summary, brief.produces_path, issues, 0.8 if ok else 0.5), text

    def _deal_memo(self, brief: Brief, consumed: dict) -> tuple[dict, str]:
        intake = json.loads(consumed["intake"])
        pack = json.loads(consumed["research"])
        m = intake.get("key_metrics", {})
        high = [r for r in pack["risks"] if r["severity"] == "high"]
        leverage = m.get("leverage_x")
        if intake.get("deal_size_usd") is None:
            rec = "Advance with conditions"
        elif len(high) >= 2 or (leverage and leverage > 6.5):
            rec = "Pass"
        elif high:
            rec = "Advance with conditions"
        else:
            rec = "Advance to diligence"
        size = intake.get("deal_size_usd")
        if self.inject.get("deal-memo") == "figure_mismatch" and size:
            size = size * 1.1  # the memo misstates the deal size; QA should catch it
        lines = [
            f"# {intake['deal_name']} - screening memo",
            "",
            "## Recommendation",
            f"**{rec}.** {len(high)} high-severity risk(s) identified; leverage {leverage or 'not provided'}x. [S1]",
            "",
            "## The deal",
            f"- Asset class: {intake.get('asset_class') or 'not provided'}; strategy: {intake.get('strategy') or 'not provided'}",
            f"- Sector: {intake.get('sector') or 'not provided'}; geography: {intake.get('geography') or 'not provided'}",
            f"- Size: {fmt_usd(size)}; our check: {fmt_usd(intake.get('check_size_usd'))}",
            f"- Sponsor: {intake.get('sponsor') or 'not provided'}; deadline: {intake.get('deadline') or 'not provided'}",
            "",
            "## Why it could work",
            f"- EBITDA margin {m.get('ebitda_margin', 'not provided')} against sector view. [S1]",
            f"- Target IRR {m.get('irr_target', 'not provided')}. [S1]",
            f"- {pack['sector_view'].split('.')[0]}. [S1]",
            "",
            "## What could go wrong",
            *[f"- ({r['severity']}) {r['risk']} Mitigant: {r['mitigant']} [S1]" for r in pack["risks"] if r["severity"] != "low"],
            "",
            "## Comparables",
            "| Name | Year | EV/EBITDA | Source |",
            "|---|---|---|---|",
            *[f"| {c['name']} | {c['year']} | {c['ev_ebitda_x']}x | [S1] |" for c in pack["comparables"]],
            "",
            "## Open questions",
            *[f"{i}. {q}" for i, q in enumerate(pack["diligence_questions"], 1)],
            "",
            "## Sources",
            *[f"- [S1] {s}" for s in pack["sources"][:1]],
        ]
        text = "\n".join(lines) + "\n"
        Path(brief.produces_path).write_text(text)
        status = "needs_review" if intake.get("deal_size_usd") is None else "ok"
        return _report("deal-memo", status, f"Memo written; recommendation: {rec}.", brief.produces_path, [], 0.75, recommendation=rec), text

    def _deal_qa(self, brief: Brief, consumed: dict) -> tuple[dict, str]:
        intake = json.loads(consumed["intake"])
        memo = consumed["memo"]
        issues = []
        for section in ["Recommendation", "The deal", "Why it could work", "What could go wrong", "Comparables", "Open questions", "Sources"]:
            if f"## {section}" not in memo:
                issues.append({"code": "section.missing", "severity": "high", "message": f"section '{section}' missing"})
        size = intake.get("deal_size_usd")
        if size is not None and f"Size: {fmt_usd(size)}" not in memo:
            stated = re.search(r"Size: ([^;]+);", memo)
            issues.append({"code": "figure.mismatch", "severity": "high", "message": f"memo states size {stated.group(1) if stated else '?'}, intake has {fmt_usd(size)}"})
        cited = set(re.findall(r"\[(S\d+)\]", memo.split("## Sources")[0]))
        listed = set(re.findall(r"- \[(S\d+)\]", memo.split("## Sources")[-1]))
        for c in sorted(cited - listed):
            issues.append({"code": "citation.dangling", "severity": "medium", "message": f"[{c}] cited but not listed under Sources"})
        if size is None:
            issues.append({"code": "recommendation.inconsistent", "severity": "medium", "message": "recommendation made without a deal size"})
        status = "failed" if any(i["severity"] == "high" for i in issues) else "needs_review" if issues else "ok"
        checks = ["section.missing", "figure.mismatch", "citation.dangling", "recommendation.inconsistent", "pii.present"]
        result = {"checks_run": checks, "issues": issues}
        text = json.dumps(result, indent=2)
        Path(brief.produces_path).write_text(text)
        summary = "All checks passed." if not issues else f"{len(issues)} issue(s): " + ", ".join(sorted({i['code'] for i in issues}))
        return _report("deal-qa", status, summary, brief.produces_path, issues, 0.85, checks_run=checks), text
