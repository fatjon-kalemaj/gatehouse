"""Command line: `gatehouse <command>`. Run `gatehouse --help` for the list."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from .gateway import Gateway
from .ledger import Ledger
from .registry import Registry

LEDGER_README = """# usage-logs

This branch is written only by gatehouse-bot. It holds every run record and the governance files.
Nothing here is ever rewritten: run logs and the audit trail are append-only and hash-chained, and
`gatehouse verify` checks both the chains and the commit history.

- `logs/runs/YYYY-MM.jsonl` - one JSON record per run
- `governance/allowlist.yaml` - who may run which pipeline
- `governance/policy.yaml` - budgets, approvals, input screening
- `governance/audit.jsonl` - denials, policy violations, approvals and governance changes
"""


def _repo(args) -> Path:
    return Path(args.repo).resolve()


def cmd_ledger_init(args) -> int:
    repo = _repo(args)
    tip = Ledger(repo).init(repo / "governance" / "seed", LEDGER_README)
    print(f"created usage-logs at {tip[:10]}")
    return 0


def cmd_run(args) -> int:
    inject = dict(item.split(":", 1) for item in args.inject_failure or [])
    record = Gateway(_repo(args)).run(
        user=args.user,
        pipeline=args.pipeline,
        input_path=args.input,
        backend=args.backend,
        approval_ticket=args.approval,
        inject=inject,
    )
    if args.json:
        print(json.dumps(record, indent=2))
        return 0
    print(f"{record['run_id']}  {record['status'].upper()}  user={record['user']}  cost=${record['cost_usd']:.2f}")
    for s in record["steps"]:
        print(f"  {s['index']}. {s['agent']:<14} {s['status']:<13} {s['duration_ms'] / 1000:6.1f}s  ${s['cost_usd']:.3f}  {s.get('skill_commit', '')[:10]}  {s['summary']}")
    for e in record.get("errors") or []:
        print(f"  ! step {e['step']}: {e['code']} - {e['message']}")
    if record.get("output") and record["output"].get("artifact"):
        print(f"  memo: .gatehouse/runs/{record['run_id']}/memo.md")
    return 0 if record["status"] in ("succeeded", "needs_review") else 1


def cmd_verify(args) -> int:
    report = Ledger(_repo(args)).verify()
    print(f"commits={report.commits} runs={report.runs} audit_events={report.audit_events}")
    for e in report.errors:
        print(f"  FAIL {e}")
    print("ledger OK" if report.ok else "ledger FAILED")
    return 0 if report.ok else 1


def cmd_registry(args) -> int:
    reg = Registry(_repo(args))
    print(f"repo commit {reg.repo_commit()[:10]}")
    for a in reg.agents.values():
        for s in a.skills:
            v = reg.skill_version(s)
            role = "orchestrator" if a.roster else "worker"
            print(f"  {a.name:<24} {role:<12} {a.model:<16} effort={a.effort:<6} skill={s:<24} {v.commit[:10]} tree={v.tree[:10]}{' (dirty)' if v.dirty else ''}")
    problems = reg.validate()
    for p in problems:
        print(f"  PROBLEM {p}")
    return 1 if problems else 0


def cmd_grant(args, grant: bool) -> int:
    event = Gateway(_repo(args)).change_allowlist(args.actor, args.user, args.group, grant)
    print(f"{event['type']}: {event['detail']}")
    return 0


def cmd_seed(args) -> int:
    from .seed import seed_history

    end = datetime.fromisoformat(args.end).replace(tzinfo=timezone.utc) if args.end else datetime.now(timezone.utc)
    n = seed_history(_repo(args), days=args.days, runs=args.runs, end=end, seed=args.seed)
    print(f"appended {n} synthetic runs to usage-logs")
    return 0


def cmd_dashboard(args) -> int:
    from .dashboard import build

    out = build(_repo(args), Path(args.out))
    print(f"wrote {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="gatehouse", description="Governed multi-agent pipelines on Claude.")
    p.add_argument("--repo", default=".", help="path to the gatehouse repo (default: .)")
    sub = p.add_subparsers(dest="cmd", required=True)

    led = sub.add_parser("ledger", help="manage the usage-logs branch")
    led.add_argument("action", choices=["init"])
    led.set_defaults(fn=cmd_ledger_init)

    run = sub.add_parser("run", help="run a pipeline through the gateway")
    run.add_argument("pipeline")
    run.add_argument("--input", required=True)
    run.add_argument("--user", required=True, help="the employee identity (from SSO in production)")
    run.add_argument("--backend", choices=["dry-run", "managed"], default="dry-run")
    run.add_argument("--approval", help="approval ticket id, for runs that policy holds for approval")
    run.add_argument("--inject-failure", action="append", metavar="AGENT:KIND",
                     help="dry run only: error | invalid_report | figure_mismatch | expensive")
    run.add_argument("--json", action="store_true")
    run.set_defaults(fn=cmd_run)

    sub.add_parser("verify", help="check hash chains and append-only history").set_defaults(fn=cmd_verify)
    sub.add_parser("registry", help="list agents, skills and their versions").set_defaults(fn=cmd_registry)

    for name, grant in (("grant", True), ("revoke", False)):
        g = sub.add_parser(name, help=f"{name} a group for a user (admins only, audited)")
        g.add_argument("--actor", required=True)
        g.add_argument("--user", required=True)
        g.add_argument("--group", required=True)
        g.set_defaults(fn=lambda a, grant=grant: cmd_grant(a, grant))

    seed = sub.add_parser("seed", help="append a synthetic history of dry runs (for the dashboard demo)")
    seed.add_argument("--days", type=int, default=30)
    seed.add_argument("--runs", type=int, default=140)
    seed.add_argument("--end", help="ISO date of the last day (default: today)")
    seed.add_argument("--seed", type=int, default=7)
    seed.set_defaults(fn=cmd_seed)

    dash = sub.add_parser("dashboard", help="build the static dashboard from usage-logs")
    dash.add_argument("--out", default="dashboard/dist/index.html")
    dash.set_defaults(fn=cmd_dashboard)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
