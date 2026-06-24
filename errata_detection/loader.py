"""Load and flatten cards.json into chronological order.

cards.json nests fow > clusters[] > sets[] > cards[]. The file order is
chronological, so the flattened list preserves print order: the first time a
card name appears is its original printing, the last is its most recent.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache

from . import config


@dataclass
class Card:
    id: str
    name: str
    abilities: list[str]
    rarity: str
    set_code: str
    set_name: str
    cluster: str
    cluster_index: int
    # Position in the global chronological flatten — lower is earlier.
    order: int
    raw: dict = field(repr=False, default_factory=dict)

    @property
    def is_xr(self) -> bool:
        """Extension Rule cards: vertically rotated, full-card rules text."""
        return self.rarity == "XR"

    @property
    def is_basic_magic_stone(self) -> bool:
        """Basic mana stones. Their type was renamed 'Magic Stone' -> 'Basic
        Magic Stone' over time; the rules text never changed, so skip them.
        'Special Magic Stone' / 'True Magic Stone' are NOT basic and are kept."""
        types = {str(t).strip().lower() for t in (self.raw.get("type") or [])}
        return bool(types & {"magic stone", "basic magic stone"})


def load_cards() -> list[Card]:
    """Return all cards in chronological print order."""
    data = json.loads(config.CARDS_JSON.read_text(encoding="utf-8"))
    cards: list[Card] = []
    order = 0
    for ci, cluster in enumerate(data["fow"]["clusters"]):
        for s in cluster["sets"]:
            for c in s["cards"]:
                cards.append(
                    Card(
                        id=c["id"],
                        name=c["name"],
                        abilities=list(c.get("abilities") or []),
                        rarity=c.get("rarity", ""),
                        set_code=s.get("code", ""),
                        set_name=s.get("name", ""),
                        cluster=cluster.get("name", ""),
                        cluster_index=ci,
                        order=order,
                        raw=c,
                    )
                )
                order += 1
    return cards


def group_by_name(cards: list[Card]) -> dict[str, list[Card]]:
    """Map card name -> printings, each list kept in chronological order."""
    groups: dict[str, list[Card]] = {}
    for c in cards:
        groups.setdefault(c.name, []).append(c)
    for prints in groups.values():
        prints.sort(key=lambda c: c.order)
    return groups


def physical_id(card: Card) -> str:
    """Front and back of one double-sided card share an id apart from the
    trailing '*' (e.g. TEU-009 front, TEU-009* back) — same physical card."""
    return card.id[:-1] if card.id.endswith("*") else card.id


def collapse_sides(prints: list[Card]) -> list[Card]:
    """Collapse same-physical-card sides to one representative (prefer the front,
    i.e. the non-'*' side), keeping chronological order."""
    rep: dict[str, Card] = {}
    for p in prints:
        pid = physical_id(p)
        cur = rep.get(pid)
        if cur is None or (cur.id.endswith("*") and not p.id.endswith("*")):
            rep[pid] = p
    return sorted(rep.values(), key=lambda c: c.order)


def is_alternative_print(card: Card) -> bool:
    """Alternative (alt-art / double-faced rainbow) card: a '*' id suffix, or a
    '[Alternative]' marker in the name (e.g. EDL-028 'Hoelle Pig [Alternative]',
    which has no '*' in its id)."""
    return card.id.endswith("*") or "[alternative]" in card.name.lower()


def is_j_ruler(card: Card) -> bool:
    """A J-Ruler is the Judgment back side of a Ruler/Regalia and often shares
    its name (e.g. CST-015 'Brandhardt' Ruler / CST-016J 'Brandhardt' J-Ruler)."""
    types = {str(t).strip().lower() for t in (card.raw.get("type") or [])}
    return "j-ruler" in types or card.id.endswith("J")


def comparison_groups(prints: list[Card]) -> list[list[Card]]:
    """Split a name's printings into independently-comparable groups so opposite
    sides of one physical card are never compared: J-Ruler (back) prints vs the
    rest, each collapsed for '*' front/back. Returns chronological groups; each
    is the reprint history of one actual card."""
    groups = []
    for side in ([p for p in prints if not is_j_ruler(p)],
                 [p for p in prints if is_j_ruler(p)]):
        if side:
            groups.append(collapse_sides(side))
    return groups


@lru_cache(maxsize=1)
def load_image_cache() -> dict[str, str]:
    """Map card id -> image URL for cards with available images."""
    return json.loads(config.IMAGE_CACHE_JSON.read_text(encoding="utf-8"))
