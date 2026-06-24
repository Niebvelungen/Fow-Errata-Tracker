"""Errata detection via reprints.

For each card name, the earliest printing holds the original text and the latest
printing holds the current (errata) text. After heuristic normalization, if the
two still differ, it's flagged as a candidate errata.
"""
from __future__ import annotations

import re

from . import config
from .loader import Card, comparison_groups, group_by_name, physical_id
from .normalize import sorted_tokens


def _cost_symbols(cost: str | None) -> list[str]:
    """Order-insensitive bag of cost symbols, e.g. '{W}{1}' -> ['1','W']."""
    return sorted(re.findall(r"\{([^}]*)\}", cost or ""))


def _race_set(card: Card) -> set[str]:
    # Drop blanks so [''] and [] (both "no race") compare equal.
    return {s for s in (str(r).strip().lower() for r in (card.raw.get("race") or [])) if s}


def detect(cards: list[Card]) -> list[dict]:
    """Return reprint-based errata candidates. Each name is split into
    independently-comparable groups (J-Ruler back vs front, '*' sides collapsed)
    so opposite sides of one physical card are never compared."""
    groups = group_by_name(cards)
    blacklist = config.load_blacklist()
    errata: list[dict] = []

    for name, prints in groups.items():
        for group in comparison_groups(prints):
            if len(group) < 2:
                continue
            og = group[0]
            latest = group[-1]
            if og.id == latest.id or physical_id(og) == physical_id(latest):
                continue
            if "R:" + latest.id in blacklist:
                continue  # reviewed as "No change"
            if og.is_basic_magic_stone or latest.is_basic_magic_stone:
                continue  # basic mana stones never changed (type rename only)

            # What changed between the original and the latest print? Text is
            # order-insensitive (same words reordered is a format shift, not
            # errata); race and cost changes also count as errata.
            changed: list[str] = []
            if sorted_tokens(og.abilities) != sorted_tokens(latest.abilities):
                changed.append("text")
            if _race_set(og) != _race_set(latest):
                changed.append("race")
            if _cost_symbols(og.raw.get("cost")) != _cost_symbols(latest.raw.get("cost")):
                changed.append("cost")
            if not changed:
                continue

            errata.append(
                {
                    "card_name": name,
                    "source": "reprint",
                    "changed": changed,
                    "og_id": og.id,
                    "og_set": f"{og.set_code} — {og.set_name}",
                    "og_cluster": og.cluster,
                    "og_text": og.abilities,
                    "og_race": og.raw.get("race") or [],
                    "og_cost": og.raw.get("cost") or "",
                    "errata_id": latest.id,
                    "errata_set": f"{latest.set_code} — {latest.set_name}",
                    "errata_cluster": latest.cluster,
                    "errata_text": latest.abilities,
                    "errata_race": latest.raw.get("race") or [],
                    "errata_cost": latest.raw.get("cost") or "",
                    "all_prints": [
                        {"id": p.id, "set": p.set_code, "text": p.abilities}
                        for p in group
                    ],
                }
            )

    errata.sort(key=lambda e: e["errata_id"])
    return errata
