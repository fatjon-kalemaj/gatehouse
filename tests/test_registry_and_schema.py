import json
import re

from gatehouse.registry import Registry
from gatehouse.schema import REQUIRED, STATUSES, validate_step_result

from .conftest import ROOT


def test_definitions_are_consistent(repo):
    reg = Registry(repo)
    assert reg.validate() == []
    assert reg.orchestrator.name == "deal-desk-orchestrator"
    assert reg.orchestrator.roster == ["deal-intake", "deal-research", "deal-memo", "deal-qa"]
    assert [s.agent for s in reg.pipelines["deal-intake"].steps] == reg.orchestrator.roster


def test_every_agent_defaults_to_opus_5_5(repo):
    assert {a.model for a in Registry(repo).agents.values()} == {"claude-opus-5-5"}


def test_skill_versions_are_git_shas(repo):
    reg = Registry(repo)
    v = reg.skill_version("deal-memo")
    assert re.fullmatch(r"[0-9a-f]{40}", v.commit)
    assert re.fullmatch(r"[0-9a-f]{40}", v.tree)
    assert v.dirty is False


def test_editing_a_skill_marks_it_dirty(repo):
    (repo / "skills/deal-qa/SKILL.md").write_text("changed\n")
    assert Registry(repo).skill_version("deal-qa").dirty is True


def test_validator_matches_json_schema_file():
    schema = json.loads((ROOT / "schemas/step-result.schema.json").read_text())
    assert set(schema["required"]) == REQUIRED
    assert set(schema["properties"]["status"]["enum"]) == STATUSES


def good_report(**over):
    r = {"agent": "deal-qa", "status": "ok", "summary": "fine", "output": {"artifact": "/tmp/x"}, "issues": []}
    r.update(over)
    return r


def test_valid_report_passes():
    assert validate_step_result(good_report(), "deal-qa") == []


def test_invalid_reports_fail():
    assert validate_step_result("not json", "deal-qa")
    assert validate_step_result(good_report(agent="deal-memo"), "deal-qa")
    assert validate_step_result(good_report(status="done"), "deal-qa")
    assert validate_step_result(good_report(extra=1), "deal-qa")
    assert validate_step_result(good_report(issues=[{"code": "x", "severity": "huge", "message": "m"}]), "deal-qa")
    r = good_report()
    del r["output"]
    assert validate_step_result(r, "deal-qa")
