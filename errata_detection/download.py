"""Download card images from the image cache.

Images are saved to images/<card_id>.<ext>. Existing files are skipped, so
downloads are resumable.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

from . import config
from .loader import load_image_cache

_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": "errata-detection/1.0"})


_UNSAFE_RE = re.compile(r'[<>:"/\\|?*]')


def _safe_name(card_id: str) -> str:
    """Card ids can contain characters illegal in Windows filenames (e.g. '*')."""
    return _UNSAFE_RE.sub("_", card_id)


def image_path(card_id: str) -> Path:
    url = load_image_cache().get(card_id, "")
    ext = Path(url).suffix or ".jpg"
    return config.IMAGES_DIR / f"{_safe_name(card_id)}{ext}"


def ensure(card_id: str) -> Path | None:
    """Download one image if missing. Returns the local path, or None if no URL."""
    cache = load_image_cache()
    url = cache.get(card_id)
    if not url:
        return None
    dest = image_path(card_id)
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    config.IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    try:
        resp = _SESSION.get(url, timeout=30)
        resp.raise_for_status()
        dest.write_bytes(resp.content)
        return dest
    except requests.RequestException as exc:  # pragma: no cover - network
        print(f"  ! failed {card_id}: {exc}")
        return None


def download_many(card_ids: list[str], workers: int = 16) -> dict[str, Path]:
    """Download a set of images concurrently. Returns id -> local path."""
    result: dict[str, Path] = {}
    ids = [cid for cid in dict.fromkeys(card_ids) if cid in load_image_cache()]
    if not ids:
        return result
    config.IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(ensure, cid): cid for cid in ids}
        for fut in as_completed(futures):
            cid = futures[fut]
            path = fut.result()
            if path:
                result[cid] = path
            done += 1
            if done % 100 == 0 or done == len(ids):
                print(f"  downloaded {done}/{len(ids)}")
    return result


def download_all(workers: int = 16) -> dict[str, Path]:
    """Download every cached image."""
    return download_many(list(load_image_cache().keys()), workers=workers)
