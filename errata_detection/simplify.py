"""Remodel the reviewed export (``errata-data.json``) into a flat, display-ready
format for downstream projects.

The review export keeps one shape per detection source (reprint / ocr / web).
Consumers of the data only care about *what changed*, so every entry is folded
into the same two-sided record:

    { key, decision, source, card_name, set_code, card_id, card_image,
      changed, text_changed, race_changed, cost_changed,
      old: { label, card_id, set_code, set_name, image, text_source,
             text, text_html, text_diff, race, race_html, race_diff, cost },
      new: { ... same ... } }

Exactly two sides — ``old`` and ``new`` — for every entry, whatever the source.

``*_html`` is the same text with the changed words wrapped in ``<del>`` /
``<ins>`` (text is HTML-escaped, line breaks kept as ``\\n`` — render with
``white-space: pre-line``). ``*_diff`` is the same information as a list of
``{op, value}`` segments (``equal`` / ``delete`` / ``insert``) for frameworks
that would rather build their own nodes than inject HTML.

Which printing is "old" and which is "new" per source:

===========  ==========================  ==============================
source       old                         new
===========  ==========================  ==============================
reprint      earliest printing           latest printing (the errata)
ocr (multi)  oldest print, read by OCR   newest print, read by OCR
ocr (single) the printed card, by OCR    stored card database text
web          stored card database text   official errata text (scraped)
===========  ==========================  ==============================

(An OCR entry whose newest printing has no usable image falls back to the card
database text for the "new" side; ``new.text_source`` says which it is.)

Run it:

    python -m errata_detection.simplify                 # errata-data.json -> output/errata-simple.json
    python -m errata_detection.simplify --minimal       # drop the *_diff segment arrays
    python -m errata_detection.simplify --only-decided  # skip unreviewed / no_change
"""
from __future__ import annotations

import argparse
import html
import json
import re
from difflib import SequenceMatcher
from pathlib import Path

from . import config
from .loader import load_image_cache

# Mana/rest/trigger symbols carry no wording meaning and OCR renders them
# inconsistently, so they are canonicalized for *comparison* only — the emitted
# text always keeps the original "{W}" etc.
_SYMBOL = re.compile(r"\{[^}]*\}|⇒|>>>|>>|≫|»|≪|«")
_TOKEN = re.compile(r"\n|\S+")


def _tokens(text: str) -> list[str]:
    """Split into words, keeping newlines as their own tokens so ability lines
    survive the diff."""
    return _TOKEN.findall(text)


def _keys(tokens: list[str]) -> list[str]:
    """Comparison key per token: symbols flattened, case/punctuation-insensitive."""
    return [_SYMBOL.sub("{}", t).lower() for t in tokens]


def _join(tokens: list[str]) -> str:
    """Re-join tokens, collapsing the spaces introduced around newline tokens.
    Line breaks that fall on a segment boundary stay attached to the segment, so
    joining the segments back together with a space restores the layout."""
    return re.sub(r" ?\n ?", "\n", " ".join(tokens)).strip(" ")


def _diff(old: str, new: str) -> tuple[list[dict], list[dict]]:
    """Word-level diff. Returns (old_segments, new_segments); each segment is
    {"op": "equal"|"delete"|"insert", "value": str}."""
    a, b = _tokens(old), _tokens(new)
    sm = SequenceMatcher(None, _keys(a), _keys(b), autojunk=False)
    old_seg: list[dict] = []
    new_seg: list[dict] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        left, right = _join(a[i1:i2]), _join(b[j1:j2])
        if tag == "equal":
            if left:
                old_seg.append({"op": "equal", "value": left})
            if right:
                new_seg.append({"op": "equal", "value": right})
        else:
            if left and tag in ("delete", "replace"):
                old_seg.append({"op": "delete", "value": left})
            if right and tag in ("insert", "replace"):
                new_seg.append({"op": "insert", "value": right})
    return old_seg, new_seg


_TAG = {"equal": None, "delete": "del", "insert": "ins"}


def _to_html(segments: list[dict]) -> str:
    """Render segments to escaped HTML with <del>/<ins> around the changes."""
    out: list[str] = []
    for seg in segments:
        value = html.escape(seg["value"])
        tag = _TAG[seg["op"]]
        out.append(f"<{tag}>{value}</{tag}>" if tag else value)
    return _join_html(out)


def _join_html(parts: list[str]) -> str:
    return re.sub(r" ?\n ?", "\n", " ".join(p for p in parts if p)).strip()


def _race_diff(old: list, new: list) -> tuple[list[dict], list[dict]]:
    """Same idea as _diff but over race entries, which are whole items rather
    than words: {"op": "equal"|"delete"|"insert", "value": "Weapon"}."""
    a = [str(r) for r in old or []]
    b = [str(r) for r in new or []]
    sm = SequenceMatcher(None, [x.lower() for x in a], [x.lower() for x in b], autojunk=False)
    old_seg: list[dict] = []
    new_seg: list[dict] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            old_seg += [{"op": "equal", "value": x} for x in a[i1:i2]]
            new_seg += [{"op": "equal", "value": x} for x in b[j1:j2]]
        else:
            if tag in ("delete", "replace"):
                old_seg += [{"op": "delete", "value": x} for x in a[i1:i2]]
            if tag in ("insert", "replace"):
                new_seg += [{"op": "insert", "value": x} for x in b[j1:j2]]
    return old_seg, new_seg


def _race_html(segments: list[dict]) -> str:
    parts = []
    for seg in segments:
        value = html.escape(seg["value"])
        tag = _TAG[seg["op"]]
        parts.append(f"<{tag}>{value}</{tag}>" if tag else value)
    return ", ".join(parts)


def _text(value) -> str:
    """Entry text fields are a list of ability lines (sometimes a bare string)."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return "\n".join(str(v).strip() for v in value if str(v).strip())


def _split_set(label: str) -> tuple[str, str]:
    """'CMF — The Crimson Moon Fairy Tale' -> ('CMF', 'The Crimson Moon Fairy Tale')."""
    for sep in (" — ", " - "):
        if sep in (label or ""):
            code, _, name = label.partition(sep)
            return code.strip(), name.strip()
    return (label or "").strip(), ""


def _side(label: str, card_id: str | None, set_label: str, text: str,
          race: list | None, cost: str | None, images: dict[str, str],
          text_source: str = "database") -> dict:
    set_code, set_name = _split_set(set_label)
    return {
        "label": label,
        "card_id": card_id,
        "set_code": set_code,
        "set_name": set_name,
        "image": images.get(card_id or "") or None,
        # Where this side's wording comes from: "print" (the card database's text
        # for that printing), "ocr" (read off the card image), "web" (scraped
        # official errata) or "database" (current stored text).
        "text_source": text_source,
        "text": text,
        "race": [str(r) for r in race] if race is not None else None,
        "cost": cost,
    }


def _sides(entry: dict, images: dict[str, str]) -> tuple[dict, dict, list[str]]:
    """Map a source-specific entry onto (old_side, new_side, changed)."""
    source = entry["source"]

    if source == "reprint":
        old = _side("Original print", entry["og_id"], entry.get("og_set", ""),
                    _text(entry.get("og_text")), entry.get("og_race") or [],
                    entry.get("og_cost") or "", images, "print")
        new = _side("Errata print", entry["errata_id"], entry.get("errata_set", ""),
                    _text(entry.get("errata_text")), entry.get("errata_race") or [],
                    entry.get("errata_cost") or "", images, "print")
        return old, new, list(entry.get("changed") or ["text"])

    if source == "ocr":
        if entry.get("single"):
            # One printing only: what the card image actually says vs the text
            # stored in the card database.
            printed = entry.get("newest_ocr") or entry.get("oldest_ocr") or ""
            old = _side("Printed card (OCR)", entry["newest_id"], entry.get("newest_set", ""),
                        _text(printed), None, None, images, "ocr")
            new = _side("Card database text", entry["newest_id"], entry.get("newest_set", ""),
                        _text(entry.get("stored_json_text")), None, None, images)
            new["image"] = None  # same card — the database text isn't a printing
            return old, new, ["text"]
        old = _side("Oldest print (OCR)", entry["oldest_id"], entry.get("oldest_set", ""),
                    _text(entry.get("oldest_ocr")), None, None, images, "ocr")
        if entry.get("newest_ocr"):
            new = _side("Newest print (OCR)", entry["newest_id"], entry.get("newest_set", ""),
                        _text(entry["newest_ocr"]), None, None, images, "ocr")
        else:
            # The newest print wasn't OCR'd (no usable image) — the card database
            # text is the current wording, so it stands in as the "new" side.
            new = _side("Card database text", entry["newest_id"], entry.get("newest_set", ""),
                        _text(entry.get("stored_json_text")), None, None, images)
        return old, new, ["text"]

    if source == "web":
        old = _side("Card database text", entry["errata_id"], entry.get("errata_set", ""),
                    _text(entry.get("json_text")), None, None, images)
        new = _side("Official errata", entry["errata_id"], entry.get("errata_set", ""),
                    _text(entry.get("web_text")), None, None, images, "web")
        new["image"] = None  # same card, no new printing
        return old, new, ["text"]

    raise ValueError(f"unknown source: {source!r}")


def simplify_entry(entry: dict, images: dict[str, str], minimal: bool = False) -> dict:
    """Fold one review-export entry into the flat two-sided display record."""
    old, new, changed = _sides(entry, images)

    old_diff, new_diff = _diff(old["text"], new["text"])
    old["text_html"], new["text_html"] = _to_html(old_diff), _to_html(new_diff)

    if old["race"] is not None or new["race"] is not None:
        old_race, new_race = _race_diff(old["race"] or [], new["race"] or [])
        old["race_html"], new["race_html"] = _race_html(old_race), _race_html(new_race)
    else:
        old_race = new_race = None
        old["race_html"] = new["race_html"] = None

    if not minimal:
        old["text_diff"], new["text_diff"] = old_diff, new_diff
        old["race_diff"], new["race_diff"] = old_race, new_race

    simple = {
        "key": entry["key"],
        "decision": entry.get("decision") or "unreviewed",
        "source": entry["source"],
        "card_name": entry["card_name"],
        "set_code": entry.get("set_code", ""),
        "card_id": entry.get("errata_id") or new["card_id"],
        "card_image": new["image"] or old["image"],
        "changed": changed,
        "text_changed": old["text"] != new["text"],
        "race_changed": "race" in changed,
        "cost_changed": "cost" in changed,
        "old": old,
        "new": new,
    }
    if entry.get("web_url"):
        simple["source_url"] = entry["web_url"]
    return simple


def simplify(data: dict, minimal: bool = False, only_decided: bool = False) -> dict:
    """Remodel a whole ``errata-data.json`` payload."""
    images = load_image_cache()
    entries = []
    for entry in data.get("entries", []):
        decision = entry.get("decision") or "unreviewed"
        if only_decided and decision in ("unreviewed", "no_change"):
            continue
        entries.append(simplify_entry(entry, images, minimal=minimal))

    counts: dict[str, int] = {}
    for e in entries:
        counts[e["decision"]] = counts.get(e["decision"], 0) + 1
    return {
        "format": "fow-errata-simple/1",
        "count": len(entries),
        "counts_by_decision": counts,
        "entries": entries,
    }


def build(src: Path | None = None, dest: Path | None = None,
          minimal: bool = False, only_decided: bool = False) -> Path:
    """Read the review export and write the simplified one. Returns the path."""
    src = Path(src) if src else config.ERRATA_DATA_JSON
    if not src.exists() and config.DEFAULT_ERRATA_DATA.exists():
        src = config.DEFAULT_ERRATA_DATA
    dest = Path(dest) if dest else config.ERRATA_SIMPLE_JSON

    data = json.loads(src.read_text(encoding="utf-8"))
    out = simplify(data, minimal=minimal, only_decided=only_decided)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return dest


def main() -> None:
    ap = argparse.ArgumentParser(description="Remodel errata-data.json into the simplified display format.")
    ap.add_argument("--input", default=None, help="source export (default: errata-data.json)")
    ap.add_argument("--output", default=None, help="destination (default: output/errata-simple.json)")
    ap.add_argument("--minimal", action="store_true", help="omit the *_diff segment arrays (HTML only)")
    ap.add_argument("--only-decided", action="store_true", help="keep only errata / format_change entries")
    args = ap.parse_args()

    path = build(args.input, args.output, minimal=args.minimal, only_decided=args.only_decided)
    data = json.loads(path.read_text(encoding="utf-8"))
    counts = " · ".join(f"{v} {k}" for k, v in sorted(data["counts_by_decision"].items()))
    print(f"Wrote {path} — {data['count']} entries ({counts})")


if __name__ == "__main__":
    main()
