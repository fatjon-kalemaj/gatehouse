"""A synthetic month of usage for the dashboard demo.

Every run goes through the real gateway, policy checks, dry-run orchestrator and ledger; only the
clock, the user and the choice of deal are picked here. Records carry `origin: "seed"`.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .gateway import Gateway

USERS = [
    ("maya.patel@example.com", 9),
    ("daniel.okafor@example.com", 7),
    ("sofia.reyes@example.com", 6),
    ("liam.chen@example.com", 5),
    ("ava.goldberg@example.com", 3),
    ("noah.kim@example.com", 2),
    ("ops.lead@example.com", 1),
    ("temp.analyst@example.com", 1),  # not on the allowlist: every attempt is denied
    ("emma.walsh@example.com", 2),  # granted deal-team partway through the month
]
DEALS = [
    ("alder-industrial-services.json", 7),
    ("birch-healthcare-credit.json", 6),
    ("cedar-logistics-re.json", 6),
    ("driftwood-digital-infra.json", 3),  # over the approval threshold
    ("elm-specialty-chemicals.json", 3),  # missing fields, research is thin
    ("fir-blocked-pii.json", 1),  # blocked by input screening
]
FAULTS = [  # (agent, kind, probability)
    ("deal-research", "error", 0.04),
    ("deal-memo", "figure_mismatch", 0.05),
    ("deal-intake", "invalid_report", 0.02),
    ("deal-research", "expensive", 0.02),
]


def _pick(rng: random.Random, weighted):
    items, weights = zip(*weighted)
    return rng.choices(items, weights=weights, k=1)[0]


def _timestamps(rng: random.Random, days: int, runs: int, end: datetime) -> list[datetime]:
    start = (end - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    out = []
    while len(out) < runs:
        day = start + timedelta(days=rng.randrange(days))
        weekend = day.weekday() >= 5
        if weekend and rng.random() < 0.85:
            continue
        # 08:00-19:00 New York, stored in UTC; a little growth toward the end of the month
        if rng.random() < 0.35 * (day - start).days / days:
            day = end.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=rng.randrange(min(days, 10)))
        out.append(day + timedelta(hours=12 + rng.uniform(0, 11)))
    return sorted(t for t in out if t <= end)


def seed_history(repo: Path, days: int, runs: int, end: datetime, seed: int = 7) -> int:
    rng = random.Random(seed)
    gw = Gateway(repo)
    stamps = _timestamps(rng, days, runs, end)
    grant_at = stamps[len(stamps) * 2 // 5]
    granted = False
    count = 0
    for i, ts in enumerate(stamps):
        if not granted and ts >= grant_at:
            gw.change_allowlist("ops.lead@example.com", "emma.walsh@example.com", "deal-team", True, now=ts - timedelta(minutes=5))
            granted = True
        user = _pick(rng, USERS)
        deal = _pick(rng, DEALS)
        approval = None
        if deal.startswith("driftwood") and rng.random() < 0.7:
            approval = f"APR-{1000 + i}"
        inject = {agent: kind for agent, kind, p in FAULTS if rng.random() < p}
        gw.run(
            user=user,
            pipeline="deal-intake",
            input_path=repo / "examples" / "deals" / deal,
            approval_ticket=approval,
            inject=inject,
            now=ts.astimezone(timezone.utc),
            run_id=f"run_{ts:%Y%m%d}_{rng.getrandbits(32):08x}",
            origin="seed",
        )
        count += 1
    return count
