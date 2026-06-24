"""Errata for Alice Origin [Stranger] Rulers, scraped from forceofwind.online.

These rulers received errata that are neither in cards.json nor on the card
images; the corrected text is maintained on the website. We fetch each card
page, read the ability text out of the `card-text-info ability-text` block,
ignore parenthetical reminder text, and compare the part about the "Stranger
deck" against the stored JSON to surface the (small) wording change.
"""
from __future__ import annotations

import json
import re
from html.parser import HTMLParser

import requests

from . import config
from .loader import Card, load_cards
from .normalize import normalize

WEBSITE = "https://www.forceofwind.online/card/{}/"
_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": "Mozilla/5.0 (errata-detection)"})


class _AbilityTextParser(HTMLParser):
    """Collect the text of each <div class="card-text-info-text"> inside the
    <div class="card-text-info ability-text"> block (skipping the "Text:" title),
    dropping all nested tags but keeping their text (e.g. the [Stranger] bubble
    word and referenced-card names)."""

    def __init__(self) -> None:
        super().__init__()
        self.depth = 0
        self.in_ability = False
        self.ability_depth = 0
        self.in_text = False
        self.text_depth = 0
        self.cur: list[str] = []
        self.texts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag != "div":
            return
        cls = dict(attrs).get("class", "") or ""
        self.depth += 1
        if "ability-text" in cls and "card-text-info" in cls:
            self.in_ability = True
            self.ability_depth = self.depth
        elif self.in_ability and "card-text-info-text" in cls and not self.in_text:
            self.in_text = True
            self.text_depth = self.depth
            self.cur = []

    def handle_endtag(self, tag):
        if tag != "div":
            return
        if self.in_text and self.depth == self.text_depth:
            self.texts.append(" ".join("".join(self.cur).split()))
            self.in_text = False
        if self.in_ability and self.depth == self.ability_depth:
            self.in_ability = False
        self.depth -= 1

    def handle_data(self, data):
        if self.in_text:
            self.cur.append(data)


def web_id(card_id: str) -> str:
    """Map a JSON card id to the website's id. Single Buy-a-Box cards are '-1'
    on the site ('the first one, since there are sometimes multiple')."""
    return card_id + "-1" if re.fullmatch(r"AO\d Buy a Box", card_id) else card_id


def targets(cards: list[Card]) -> list[Card]:
    """Alice Origin Rulers with a [Stranger] ability."""
    return [
        c
        for c in cards
        if c.cluster == "Alice Origin"
        and c.raw.get("type") == ["Ruler"]
        and any("stranger" in a.lower() for a in c.abilities)
    ]


def strip_parens(text: str) -> str:
    """Remove parenthetical reminder text, including nested parentheses."""
    prev = None
    while prev != text:
        prev = text
        text = re.sub(r"\([^()]*\)", " ", text)
    return text


def stranger_clause(text: str) -> str:
    """The ';'-separated clause that mentions the Stranger deck (after dropping
    parenthetical reminders). This is where the errata wording lives."""
    text = strip_parens(text)
    for seg in re.split(r";", text):
        if "stranger deck" in seg.lower():
            seg = seg.split(" - ")[-1]  # keep just the option, not the long prefix
            return " ".join(seg.split())
    return ""


def _load_cache() -> dict[str, str]:
    if config.STRANGER_CACHE_JSON.exists():
        return json.loads(config.STRANGER_CACHE_JSON.read_text(encoding="utf-8"))
    return {}


def _save_cache(cache: dict[str, str]) -> None:
    config.STRANGER_CACHE_JSON.write_text(
        json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8"
    )


def fetch_ability_text(wid: str, cache: dict[str, str]) -> str | None:
    """Joined text of the website's ability-text block, cached by website id."""
    if wid in cache:
        return cache[wid]
    url = WEBSITE.format(requests.utils.quote(wid))
    try:
        resp = _SESSION.get(url, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as exc:  # pragma: no cover - network
        print(f"  ! fetch failed {wid}: {exc}")
        return None
    parser = _AbilityTextParser()
    parser.feed(resp.text)
    text = " ".join(parser.texts)
    cache[wid] = text
    _save_cache(cache)
    return text


def detect(cards: list[Card] | None = None, fetch: bool = True) -> list[dict]:
    """Compare each Alice Origin [Stranger] Ruler's JSON text against the
    website's (errata) text. Returns web-errata candidates."""
    cards = cards or load_cards()
    cache = _load_cache()
    errata: list[dict] = []
    for c in targets(cards):
        wid = web_id(c.id)
        web_text = cache.get(wid)
        if web_text is None and fetch:
            web_text = fetch_ability_text(wid, cache)
        if not web_text:
            continue

        # Only the Stranger-deck part matters — leave everything else be.
        web_clause = stranger_clause(web_text)
        json_clause = stranger_clause(" ".join(c.abilities))
        if not web_clause or not json_clause:
            continue
        if normalize(web_clause) == normalize(json_clause):
            continue

        errata.append(
            {
                "card_name": c.name,
                "source": "web",
                "errata_id": c.id,
                "errata_set": f"{c.set_code} — {c.set_name}",
                "errata_cluster": c.cluster,
                "web_url": WEBSITE.format(requests.utils.quote(wid)),
                "json_text": [json_clause],
                "web_text": [web_clause],
            }
        )
    return errata
