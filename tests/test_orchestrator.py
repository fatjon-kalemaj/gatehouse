from datetime import datetime, timezone

from gatehouse.backends.dry_run import DryRunWorkers
from gatehouse.orchestrator import SequentialOrchestrator
from gatehouse.registry import Registry

START = datetime(2026, 10, 1, 15, 0, tzinfo=timezone.utc)


def run(repo, deal="alder-industrial-services.json", inject=None, budget=6.0):
    reg = Registry(repo)
    calls = []
    workers = DryRunWorkers(reg, "seed-1", inject)

    def tracking(agent, brief):
        calls.append((agent.name, sorted(brief.consumes)))
        return workers(agent, brief)

    orch = SequentialOrchestrator(reg, reg.pipelines["deal-intake"], tracking, repo / ".runs" / "r1", budget)
    return orch.run("r1", str(repo / "examples/deals" / deal), START), calls


def test_steps_run_in_order_and_each_sees_prior_artifacts(repo):
    result, calls = run(repo)
    assert result.status == "succeeded"
    assert calls == [
        ("deal-intake", ["input"]),
        ("deal-research", ["intake"]),
        ("deal-memo", ["intake", "research"]),
        ("deal-qa", ["intake", "memo", "research"]),
    ]
    assert [s["index"] for s in result.steps] == [1, 2, 3, 4]
    assert all(s["output_digest"].startswith("sha256:") for s in result.steps)


def test_worker_error_stops_the_pipeline(repo):
    result, calls = run(repo, inject={"deal-research": "error"})
    assert result.status == "failed"
    assert [c[0] for c in calls] == ["deal-intake", "deal-research"]
    assert result.errors[0]["code"] == "agent.error"


def test_invalid_report_is_retried_once_then_fails(repo):
    result, calls = run(repo, inject={"deal-intake": "invalid_report"})
    assert result.status == "failed"
    assert [c[0] for c in calls] == ["deal-intake", "deal-intake"]
    assert result.steps[0]["attempts"] == 2
    assert result.errors[0]["code"] == "schema.invalid"


def test_qa_gate_fails_a_memo_with_a_wrong_figure(repo):
    result, _ = run(repo, inject={"deal-memo": "figure_mismatch"})
    assert result.status == "failed"
    assert result.steps[-1]["agent"] == "deal-qa"
    assert {i["code"] for i in result.steps[-1]["issues"]} == {"figure.mismatch"}


def test_gate_needs_review_carries_to_the_run(repo):
    result, _ = run(repo, deal="elm-specialty-chemicals.json")
    assert result.status == "needs_review"
    assert len(result.steps) == 4


def test_budget_is_checked_before_each_step(repo):
    result, calls = run(repo, inject={"deal-research": "expensive"}, budget=0.50)
    assert result.status == "budget_exceeded"
    assert "deal-qa" not in [c[0] for c in calls]
    assert result.errors[-1]["code"] == "budget.reached"
