"""Builds the monitoring and governance dashboard: one static HTML file with the ledger data inlined.

The page reads nothing at view time, so it can be served from anywhere that is allowed to see the
logs (an internal static host, a GitHub Pages site on the private repo, or opened from disk).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .ledger import Ledger

TEMPLATE = Path(__file__).resolve().parent.parent / "dashboard" / "template.html"


def collect(repo: Path) -> dict:
    ledger = Ledger(repo)
    report = ledger.verify()
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "branch": ledger.branch,
        "tip": ledger.tip(),
        "verify": {"ok": report.ok, "commits": report.commits, "errors": report.errors[:20]},
        "runs": ledger.runs(),
        "audit": ledger.audit(),
        "allowlist": yaml.safe_load(ledger.read("governance/allowlist.yaml") or "{}"),
        "policy": yaml.safe_load(ledger.read("governance/policy.yaml") or "{}"),
    }


def build(repo: Path, out: Path) -> Path:
    data = collect(repo)
    html = TEMPLATE.read_text().replace(
        "/*__GATEHOUSE_DATA__*/null", json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    )
    out = Path(out)
    if not out.is_absolute():
        out = Path(repo) / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html)
    return out
