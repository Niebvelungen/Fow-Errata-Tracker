"""Paths and shared configuration."""
from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv

# Project root = parent of this package directory.
ROOT = Path(__file__).resolve().parent.parent

load_dotenv(ROOT / ".env")

# Input data (provided).
CARDS_JSON = ROOT / "cards.json"
IMAGE_CACHE_JSON = ROOT / "image_cache.json"

# Generated artifacts.
IMAGES_DIR = ROOT / "images"
OUTPUT_DIR = ROOT / "output"
ERRATA_JSON = OUTPUT_DIR / "errata.json"
REPORT_HTML = OUTPUT_DIR / "report.html"
OCR_CACHE_JSON = ROOT / ".ocr_cache.json"

# Entries reviewed as "No change" in the report can be exported to this file and
# committed; on the next run they are excluded from detection. Keys are the
# report's entry keys: "R:<latest_id>" (reprint) / "O:<card_id>" (ocr).
BLACKLIST_JSON = ROOT / "blacklist.json"

# Committed defaults: a baseline blacklist and a prior export of review decisions
# (errata-data.json) used to pre-seed the report when the browser has no saved
# decisions yet, so prior review work isn't lost.
DEFAULT_DIR = ROOT / "default"
DEFAULT_BLACKLIST = DEFAULT_DIR / "blacklist.json"
DEFAULT_ERRATA_DATA = DEFAULT_DIR / "errata-data.json"

# OCR (Claude vision).
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
# Sonnet is plenty for card OCR and far cheaper than Opus. Override via the
# OCR_MODEL env var — e.g. OCR_MODEL=claude-haiku-4-5 for the cheapest pass.
OCR_MODEL = os.getenv("OCR_MODEL", "claude-sonnet-4-6")

# Below this token-similarity ratio (0..1), OCR text vs JSON text counts as a
# mismatch worth flagging as a possible errata. Tuned to tolerate OCR noise.
OCR_SIMILARITY_THRESHOLD = 0.80


def have_api_key() -> bool:
    return bool(ANTHROPIC_API_KEY) and ANTHROPIC_API_KEY != "sk-ant-replace-me"


def load_blacklist() -> set[str]:
    """Keys marked 'No change' to exclude from detection — the union of the
    committed default blacklist and the working-root blacklist.json. Each file is
    a plain JSON array of keys or an object with a ``keys`` array."""
    keys: set[str] = set()
    for path in (DEFAULT_BLACKLIST, BLACKLIST_JSON):
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data = data.get("keys", [])
        keys.update(data)
    return keys


def load_default_decisions() -> dict[str, str]:
    """Prior review decisions {entry_key: 'errata'|'format_change'|'no_change'}
    from default/errata-data.json, used to pre-seed the report's review state."""
    if not DEFAULT_ERRATA_DATA.exists():
        return {}
    data = json.loads(DEFAULT_ERRATA_DATA.read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for e in data.get("entries", []):
        dec = e.get("decision")
        if dec == "not_errata":  # legacy value -> current category
            dec = "no_change"
        if dec and dec != "unreviewed":
            out[e["key"]] = dec
    return out
