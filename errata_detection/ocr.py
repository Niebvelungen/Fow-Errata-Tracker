"""OCR-based errata detection via Claude vision.

The stored JSON text is sometimes already the corrected (errata) text and
sometimes still the original — we can't trust it as the baseline. So for each
card name not already caught by the reprint pass, we OCR BOTH the oldest and the
newest printed images and treat the printed cards as the source of truth,
comparing all three sources pairwise:

    oldest image  ↔  newest image   (did the printed text change = the errata)
    oldest image  ↔  stored JSON
    newest image  ↔  stored JSON

Any disagreement is flagged, and all three are shown in the report so a human can
decide which is right. Single-printing cards fall back to image ↔ JSON.

A whole-image approach is used rather than cropping: Claude vision handles the
card layout and the rotated full-card text of XR (Extension Rule) cards, and is
told the rarity so it knows when to expect rotated text.
"""
from __future__ import annotations

import base64
import difflib
import json
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from difflib import SequenceMatcher
from pathlib import Path

from . import config, download
from .loader import Card, comparison_groups, group_by_name, is_alternative_print
from .normalize import normalize, sorted_tokens

# Common function words: a single one of these differing is almost always an OCR
# add/drop, not an errata. Game terms (fairy, vampire, flying, …) are NOT here.
_STOP = {
    "this", "that", "these", "those", "your", "their", "them", "they", "then",
    "than", "with", "into", "from", "onto", "when", "whenever", "where", "here",
    "there", "have", "has", "had", "been", "was", "were", "will", "would",
    "could", "should", "shall", "until", "while", "also", "both", "such",
    "only", "once", "even", "just", "very", "more", "less", "most", "other",
    "another", "each", "every", "same", "about", "over", "under", "being",
    "does", "done", "they", "zones", "zone", "long", "times",
}

_MEDIA = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}

_PROMPT = (
    "This is a Force of Will trading card. Extract ONLY the rules/ability text "
    "(the game effect text, usually in the lower half of the card). "
    "Do NOT include: the card name; the type/race line that sits at the top of "
    "the text box (e.g. 'Resonator', 'Resonator : Fairy Tale/Human', "
    "'Spell:Chant'); the italic flavour text; the artist credit; the ATK/DEF "
    "numbers; the cost; or the card-number/rarity line. "
    "Mana and game symbols are printed as icons — represent each icon as {} or "
    "omit it; transcribe only words. This includes generic/numeric cost icons "
    "(a number inside a crystal/diamond icon, e.g. the cost line of a Judgment "
    "or Activate ability): render those as {} too, NOT as a bare number. Numbers "
    "that are part of the sentence (damage amounts, ATK/DEF bonuses like "
    "[+200/+200]) are normal text — keep those. Return the ability text verbatim "
    "as plain text and nothing else. If there is no rules text, return an empty "
    "string."
)
_XR_NOTE = (
    " NOTE: this is an XR 'Extension Rule' card — its text is rotated 90 degrees "
    "and spans the whole card. Read the rotated text."
)


def _load_cache() -> dict[str, str]:
    if config.OCR_CACHE_JSON.exists():
        return json.loads(config.OCR_CACHE_JSON.read_text(encoding="utf-8"))
    return {}


def _save_cache(cache: dict[str, str]) -> None:
    # Atomic write so an interrupt mid-save can't corrupt the cache.
    tmp = config.OCR_CACHE_JSON.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, config.OCR_CACHE_JSON)


def _sniff_media(raw: bytes, path: Path) -> str:
    """Detect the real image type — some URLs serve a PNG with a .jpg name."""
    if raw[:8].startswith(b"\x89PNG"):
        return "image/png"
    if raw[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    if raw[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return _MEDIA.get(path.suffix.lower(), "image/jpeg")


def _ocr_image(client, card: Card, path: Path) -> str:
    raw = path.read_bytes()
    media = _sniff_media(raw, path)
    data = base64.standard_b64encode(raw).decode()
    prompt = _PROMPT + (_XR_NOTE if card.is_xr else "")
    resp = client.messages.create(
        model=config.OCR_MODEL,
        max_tokens=1024,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": media, "data": data}},
                    {"type": "text", "text": prompt},
                ],
            }
        ],
    )
    return "".join(b.text for b in resp.content if b.type == "text").strip()


# Card types that make a "{id}*" entry a double-faced Alternative card: the
# printed image shows both faces, so its text is the base "{id}" entry plus the
# "{id}*" entry combined.
_ALT_TYPES = ("resonator", "chant", "addition")


def _is_alternative(card: Card) -> bool:
    if "*" not in card.id:
        return False
    types = card.raw.get("type") or []
    return any(any(t in str(x).lower() for t in _ALT_TYPES) for x in types)


def _double_faced(card: Card, by_id: dict[str, Card]) -> bool:
    """The card prints both faces on its image (it's one half of an Alternative
    pair: it ends in '*' or has a '*' sibling)."""
    return card.id.endswith("*") or (card.id + "*") in by_id


def candidate_texts(card: Card, by_id: dict[str, Card]) -> list[list[str]]:
    """JSON ability texts to compare OCR against. A double-faced Alternative card
    prints BOTH faces on each image, so for a card that has an Alternative sibling
    (its '*' counterpart, e.g. EDL-038 'Thunder Wolf' ↔ EDL-038* 'Thunder
    [Alternative]', or its base if this card is the '*'), compare against the
    combined two-face text AS WELL AS each face alone (the image may show one or
    both). First entry is the canonical text for display."""
    other = by_id.get(card.id[:-1]) if card.id.endswith("*") else by_id.get(card.id + "*")
    if other:
        combined = list(card.abilities) + list(other.abilities)
        return [combined, card.abilities, other.abilities]
    return [card.abilities]


def similarity(a: str, b: str) -> float:
    """Order-insensitive token similarity (0..1) of two rules texts, after
    normalization. Sorting the tokens means text shifted to another point on the
    card doesn't lower the score."""
    ta, tb = sorted_tokens(a), sorted_tokens(b)
    if not ta and not tb:
        return 1.0
    return SequenceMatcher(None, ta, tb).ratio()


def _content_words(text: str) -> set[str]:
    return {w for w in normalize(text).split() if len(w) >= 4 and w.isalpha() and w not in _STOP}


_EMPTY_BRACE = re.compile(r"\{\s*\}")


def genuine_word_diff(a: str, b: str) -> set[str]:
    """Content words present on one side with NO fuzzy near-match on the other.
    Catches a real one-word errata (e.g. 'fairy' dropped, or a keyword like
    '[Inheritance]' genuinely added in a reprint) while ignoring OCR typos
    ('abilites' vs 'abilities') and verb-form variants. The ratio score can't see
    a single-word change.

    Keyword/timing abilities printed as ICONS are read by OCR as "{}". An OCR "{}"
    is a placeholder for an unreadable token (a symbol like {Rest} or a word/icon)
    — never literally empty text. Each "{}" on a side is treated as a wildcard that
    accounts for one word missing from that side, so an iconified keyword can't
    drive a false flag — but a keyword that is genuinely ABSENT (no "{}" standing
    in for it) still flags, which is the real-errata case."""
    a_words = _content_words(a)
    b_words = _content_words(b)
    a_only = [w for w in a_words - b_words if not difflib.get_close_matches(w, list(b_words), n=1, cutoff=0.8)]
    b_only = [w for w in b_words - a_words if not difflib.get_close_matches(w, list(a_words), n=1, cutoff=0.8)]
    # A "{}" on one side can stand in for a word unique to the other side.
    braces_a = len(_EMPTY_BRACE.findall(a or ""))
    braces_b = len(_EMPTY_BRACE.findall(b or ""))
    out: set[str] = set()
    if braces_b < len(a_only):
        out |= set(a_only)
    if braces_a < len(b_only):
        out |= set(b_only)
    return out


def _set_label(c: Card) -> str:
    return f"{c.set_code} — {c.set_name}"


def detect(
    cards: list[Card],
    already_flagged_names: set[str],
    limit: int | None = None,
    workers: int = 8,
) -> list[dict]:
    """OCR the oldest AND newest printing of each name (skipping reprint-flagged
    names) and flag any disagreement between oldest image, newest image, and the
    stored JSON. Results are cached per card id so re-runs only OCR new images."""
    if not config.have_api_key():
        print("  ! ANTHROPIC_API_KEY not set — skipping OCR phase.")
        return []

    import anthropic  # local import so the rest of the tool runs without the SDK

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY, max_retries=5)
    image_cache = download.load_image_cache()
    cache = _load_cache()
    blacklist = config.load_blacklist()
    by_id = {c.id: c for c in cards}
    groups = group_by_name(cards)

    # One plan per comparable group (front vs J-Ruler back are separate cards):
    # oldest and newest printing of each.
    plans: list[tuple[str, Card, Card, list[str]]] = []
    for name, prints in groups.items():
        if name in already_flagged_names:
            continue
        for group in comparison_groups(prints):
            oldest, newest = group[0], group[-1]
            # Skip Alternative cards: their image shows both faces but the JSON is
            # one side, so OCR can't compare cleanly — and none carry errata.
            if is_alternative_print(newest) or is_alternative_print(oldest):
                continue
            if newest.is_basic_magic_stone or not newest.abilities:
                continue
            if "O:" + newest.id in blacklist:
                continue
            ids = [i for i in dict.fromkeys([oldest.id, newest.id]) if i in image_cache]
            if not ids:
                continue
            plans.append((name, oldest, newest, ids))
    if limit:
        plans = plans[:limit]

    all_ids = {i for _, _, _, ids in plans for i in ids}
    need = [i for i in all_ids if i not in cache]
    print(f"  OCR: {len(plans)} cards, {len(all_ids)} images ({len(all_ids) - len(need)} cached, {len(need)} to OCR)")

    lock = threading.Lock()
    progress = {"done": 0}

    def work(card_id: str) -> None:
        path = download.ensure(card_id)
        if not path:
            text = ""  # unreachable image -> empty so we don't retry forever
        else:
            try:
                text = _ocr_image(client, by_id[card_id], path)
            except Exception as exc:  # pragma: no cover - network/API
                print(f"  ! OCR failed {card_id}: {exc}")
                return
        with lock:
            cache[card_id] = text
            progress["done"] += 1
            n = progress["done"]
            if n % 25 == 0 or n == len(need):
                print(f"  OCR {n}/{len(need)}")
            if n % 50 == 0:
                _save_cache(cache)

    if need:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for fut in as_completed([pool.submit(work, i) for i in need]):
                fut.result()
        _save_cache(cache)

    thr = config.OCR_SIMILARITY_THRESHOLD
    errata: list[dict] = []
    for name, oldest, newest, _ids in plans:
        # Treat a failed/empty OCR ("") as missing so it doesn't read as "all words dropped".
        old_ocr = (cache.get(oldest.id) or None) if oldest.id in image_cache else None
        new_ocr = (cache.get(newest.id) or None) if newest.id in image_cache else None
        single = oldest.id == newest.id
        cmp = candidate_texts(newest, by_id)  # JSON candidates (handles Alternative)
        cmp_join = [" ".join(x) for x in cmp]

        def vs_json_sim(t: str | None) -> float | None:
            return None if t is None else max(similarity(t, c) for c in cmp_join)

        def vs_json_words(t: str | None) -> set[str]:
            # Genuine words against the best-matching JSON candidate (alt-aware).
            return set() if t is None else min((genuine_word_diff(t, c) for c in cmp_join), key=len)

        sim_oj = vs_json_sim(old_ocr)
        sim_nj = vs_json_sim(new_ocr)
        # Only compare the two printed images directly when they're the same kind
        # of print — comparing a single-face image against a double-faced
        # Alternative print (one shows both faces) is apples-to-oranges.
        compare_imgs = (
            not single
            and old_ocr is not None
            and new_ocr is not None
            and _double_faced(oldest, by_id) == _double_faced(newest, by_id)
        )
        sim_on = similarity(old_ocr, new_ocr) if compare_imgs else None
        sims = [s for s in (sim_oj, sim_nj, sim_on) if s is not None]
        if not sims:
            continue

        diff_words: set[str] = set()
        diff_words |= vs_json_words(old_ocr)
        diff_words |= vs_json_words(new_ocr)
        if compare_imgs:
            diff_words |= genuine_word_diff(old_ocr, new_ocr)

        # Flag on a big overall difference (ratio) OR a genuine single-word change.
        if min(sims) >= thr and not diff_words:
            continue

        def r(x):
            return round(x, 3) if x is not None else None

        errata.append(
            {
                "card_name": name,
                "source": "ocr",
                "errata_id": newest.id,
                "errata_set": _set_label(newest),
                "errata_cluster": newest.cluster,
                "rarity": newest.rarity,
                "alternative": _is_alternative(newest),
                "single": single,
                "oldest_id": oldest.id,
                "oldest_set": _set_label(oldest),
                "oldest_ocr": old_ocr,
                "newest_id": newest.id,
                "newest_set": _set_label(newest),
                "newest_ocr": new_ocr,
                "json_text": cmp[0],
                "diff_words": sorted(diff_words),
                "sim_old_new": r(sim_on),
                "sim_old_json": r(sim_oj),
                "sim_new_json": r(sim_nj),
                "similarity": r(min(sims)),
            }
        )

    # Show ratio-driven (more different) first, then single-word finds.
    errata.sort(key=lambda e: e["similarity"])
    return errata
