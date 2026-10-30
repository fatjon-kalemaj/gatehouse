import os
import subprocess
import tempfile

import pytest

from gatehouse.ledger import Ledger, LedgerConflict


def rec(i):
    return {"run_id": f"r{i}", "user": "u@example.com", "status": "succeeded", "started_at": f"2026-10-{i:02d}T12:00:00+00:00"}


def test_appends_are_chained_and_verify(ledger_repo):
    led = Ledger(ledger_repo)
    led.append(runs=[rec(1), rec(2)], message="two")
    led.append(runs=[rec(3)], audit=[{"type": "x", "at": "2026-10-03", "actor": "a", "detail": {}}])
    runs = led.runs()
    assert [r["seq"] for r in runs] == [1, 2, 3]
    assert runs[1]["prev_hash"] == runs[0]["hash"]
    report = led.verify()
    assert report.ok, report.errors
    assert report.runs == 3 and report.audit_events == 2


def test_backfilled_month_keeps_the_chain(ledger_repo):
    led = Ledger(ledger_repo)
    led.append(runs=[rec(20)])
    led.append(runs=[{**rec(1), "started_at": "2026-09-30T12:00:00+00:00"}])
    assert [r["seq"] for r in led.runs()] == [1, 2]
    assert led.verify().ok


def _commit_rewrite(repo, path, content):
    """Simulate someone rewriting history-bearing content on the branch with a normal commit."""
    env = {**os.environ, "GIT_AUTHOR_NAME": "x", "GIT_AUTHOR_EMAIL": "x@e", "GIT_COMMITTER_NAME": "x", "GIT_COMMITTER_EMAIL": "x@e"}
    run = lambda *a, **k: subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True, text=True, env=env, **k).stdout.strip()
    with tempfile.TemporaryDirectory() as tmp:
        env["GIT_INDEX_FILE"] = os.path.join(tmp, "idx")
        run("read-tree", "usage-logs")
        blob = run("hash-object", "-w", "--stdin", input=content)
        run("update-index", "--cacheinfo", f"100644,{blob},{path}")
        tree = run("write-tree")
    commit = run("commit-tree", tree, "-p", "usage-logs", "-m", "tamper")
    run("update-ref", "refs/heads/usage-logs", commit)


def test_rewriting_a_log_line_is_detected(ledger_repo):
    led = Ledger(ledger_repo)
    led.append(runs=[rec(1), rec(2)])
    path = "logs/runs/2026-10.jsonl"
    _commit_rewrite(ledger_repo, path, led.read(path).replace("succeeded", "failed", 1))
    errors = led.verify().errors
    assert any("hash does not match" in e for e in errors)
    assert any("rewrote append-only file" in e for e in errors)


def test_governance_change_without_audit_is_detected(ledger_repo):
    led = Ledger(ledger_repo)
    _commit_rewrite(ledger_repo, "governance/allowlist.yaml", "version: 1\nusers: {}\n")
    assert any("without an audit event" in e for e in led.verify().errors)


def test_append_only_files_cannot_be_replaced(ledger_repo):
    with pytest.raises(ValueError):
        Ledger(ledger_repo).append(replace={"governance/audit.jsonl": ""})


def test_compare_and_swap_rejects_a_stale_parent(ledger_repo):
    led = Ledger(ledger_repo)
    stale = led.tip()
    led.append(runs=[rec(1)])
    commit = led._commit({"x.txt": "x"}, "stale", stale, None)
    with pytest.raises(LedgerConflict):
        led._move(commit, stale)


def test_rewritten_history_fails_the_checkpoint(ledger_repo):
    led = Ledger(ledger_repo)
    led.append(runs=[rec(1)])
    checkpoint = led.tip()
    assert led.verify(since=checkpoint).ok
    led.append(runs=[rec(2)])
    assert led.verify(since=checkpoint).ok  # appending after the checkpoint is fine

    # Rewrite: drop both runs and append a different one. Hashes are recomputed, so the chain is valid.
    parent = subprocess.run(["git", "rev-parse", f"{checkpoint}~1"], cwd=ledger_repo, capture_output=True, text=True).stdout.strip()
    subprocess.run(["git", "update-ref", "refs/heads/usage-logs", parent], cwd=ledger_repo, check=True)
    led.append(runs=[{**rec(1), "status": "failed"}])
    assert led.verify().ok
    errors = led.verify(since=checkpoint).errors
    assert any("history rewritten" in e for e in errors)
