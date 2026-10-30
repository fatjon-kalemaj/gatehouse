"""Helpers for reading a deal input file. Used by the gateway (policy checks) and the dry-run intake worker."""

from __future__ import annotations

import re

SIZE_HINT = re.compile(
    r"(?:EV|enterprise value|deal size|raising|facility)[^$]{0,40}(\$\s?[\d.,]+\s*(?:bn|b|billion|mm|m|million)?)", re.I
)


def parse_usd(text: str) -> float | None:
    m = re.search(r"\$\s?([\d.,]+)\s*(bn|b|billion|mm|m|million|k)?", text, re.I)
    if not m:
        return None
    value = float(m.group(1).replace(",", ""))
    unit = (m.group(2) or "").lower()
    return value * {"bn": 1e9, "b": 1e9, "billion": 1e9, "mm": 1e6, "m": 1e6, "million": 1e6, "k": 1e3}.get(unit, 1)


def declared_size(deal: dict) -> float | None:
    """Deal size from the form field, else from the teaser text."""
    size = (deal.get("fields") or {}).get("deal_size_usd")
    if size is None:
        m = SIZE_HINT.search(deal.get("teaser", ""))
        size = parse_usd(m.group(1)) if m else None
    return size


def summarize(deal: dict) -> str:
    f = deal.get("fields") or {}
    parts = [deal.get("deal_name") or "unnamed deal", f.get("asset_class"), f.get("sector"), f.get("geography")]
    return ", ".join(p for p in parts if p)
