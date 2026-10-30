import json
from datetime import datetime, timezone

import yaml

from gatehouse.dashboard import build
from gatehouse.gateway import Gateway
from gatehouse.ledger import Ledger
from gatehouse.registry import Registry

NOW = datetime(2026, 10, 20, 15, 0, tzinfo=timezone.utc)


def test_successful_run_is_logged_with_versions(ledger_repo):
    gw = Gateway(ledger_repo)
    rec = gw.run("Maya.Patel@example.com", "deal-intake", ledger_repo / "examples/deals/alder-industrial-services.json", now=NOW)
    assert rec["status"] == "succeeded"
    stored = Ledger(ledger_repo).runs()[-1]
    assert stored["run_id"] == rec["run_id"] and stored["user"] == "maya.patel@example.com"
    reg = Registry(ledger_repo)
    assert [s["skill_commit"] for s in stored["steps"]] == [reg.skill_version(s["agent"]).commit for s in stored["steps"]]
    assert stored["orchestrator"]["skill"] == "deal-desk-orchestrator"
    assert stored["cost_usd"] > 0 and stored["tokens"]["output"] > 0
    assert stored["input"]["sha256"].startswith("sha256:")
    assert "teaser" not in json.dumps(stored)  # inputs are logged as digest + summary only
    assert Ledger(ledger_repo).verify().ok


def test_denied_run_reaches_no_agent_and_is_audited(ledger_repo):
    gw = Gateway(ledger_repo)
    rec = gw.run("maya.patel@example.com", "deal-intake", ledger_repo / "examples/deals/fir-blocked-pii.json", now=NOW)
    assert rec["status"] == "denied" and rec["steps"] == []
    types = [a["type"] for a in Ledger(ledger_repo).audit()]
    assert types[-2:] == ["policy.violation", "run.denied"]
    assert not (gw.work_dir / rec["run_id"]).exists()


def test_approval_ticket_lets_a_large_deal_through(ledger_repo):
    gw = Gateway(ledger_repo)
    deal = ledger_repo / "examples/deals/driftwood-digital-infra.json"
    assert gw.run("maya.patel@example.com", "deal-intake", deal, now=NOW)["status"] == "pending_approval"
    rec = gw.run("maya.patel@example.com", "deal-intake", deal, approval_ticket="APR-7", now=NOW)
    assert rec["status"] in ("succeeded", "needs_review")
    assert any(a["type"] == "approval.used" and a["detail"]["ticket"] == "APR-7" for a in Ledger(ledger_repo).audit())


def test_grant_changes_allowlist_with_an_audit_event(ledger_repo):
    gw = Gateway(ledger_repo)
    deal = ledger_repo / "examples/deals/alder-industrial-services.json"
    assert gw.run("new.hire@example.com", "deal-intake", deal, now=NOW)["status"] == "denied"
    gw.change_allowlist("ops.lead@example.com", "new.hire@example.com", "deal-team", True, now=NOW)
    allow = yaml.safe_load(Ledger(ledger_repo).read("governance/allowlist.yaml"))
    assert allow["users"]["new.hire@example.com"] == ["deal-team"]
    assert gw.run("new.hire@example.com", "deal-intake", deal, now=NOW)["status"] == "succeeded"
    assert Ledger(ledger_repo).verify().ok


def test_only_admins_can_grant(ledger_repo):
    import pytest

    with pytest.raises(PermissionError):
        Gateway(ledger_repo).change_allowlist("maya.patel@example.com", "x@example.com", "deal-team", True)


def test_dashboard_builds_from_the_ledger(ledger_repo):
    Gateway(ledger_repo).run("maya.patel@example.com", "deal-intake", ledger_repo / "examples/deals/cedar-logistics-re.json", now=NOW)
    out = build(ledger_repo, ledger_repo / "out.html")
    html = out.read_text()
    assert "/*__GATEHOUSE_DATA__*/" not in html
    assert '"verify":{"ok":true' in html and "Project Cedar" in html
