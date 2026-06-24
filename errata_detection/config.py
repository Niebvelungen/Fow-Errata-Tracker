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
    """Keys marked 'No change' to exclude from detection. Accepts either a plain
    JSON array of keys or an object with a ``keys`` array."""
    if not BLACKLIST_JSON.exists():
        return set()
    data = json.loads(BLACKLIST_JSON.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("keys", [])
    return set(data)
