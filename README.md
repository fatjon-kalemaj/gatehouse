# usage-logs

This branch is written only by gatehouse-bot. It holds every run record and the governance files.
Nothing here is ever rewritten: run logs and the audit trail are append-only and hash-chained, and
`gatehouse verify` checks both the chains and the commit history.

- `logs/runs/YYYY-MM.jsonl` - one JSON record per run
- `governance/allowlist.yaml` - who may run which pipeline
- `governance/policy.yaml` - budgets, approvals, input screening
- `governance/audit.jsonl` - denials, policy violations, approvals and governance changes
