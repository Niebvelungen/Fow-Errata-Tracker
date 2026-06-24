"""Force of Will errata detection — pipeline entry point.

Usage:
    python run.py                 # reprint detect + OCR (if API key) + report
    python run.py --no-ocr        # reprint detection only
    python run.py --ocr-limit 50  # cap OCR to 50 cards (cost control / smoke test)
    python run.py --download-all   # also download every card image for checking
"""
from __future__ import annotations

import argparse
import json

from errata_detection import config, download, ocr, reprint, report
from errata_detection.loader import load_cards


def main() -> None:
    ap = argparse.ArgumentParser(description="Detect Force of Will card errata.")
    ap.add_argument("--no-ocr", action="store_true", help="skip the OCR phase")
    ap.add_argument("--ocr-limit", type=int, default=None, help="max cards to OCR")
    ap.add_argument("--download-all", action="store_true", help="download every card image")
    ap.add_argument("--no-download", action="store_true", help="don't download report images")
    args = ap.parse_args()

    print("Loading cards…")
    cards = load_cards()
    print(f"  {len(cards)} cards loaded")

    print("Detecting reprint errata…")
    reprint_errata = reprint.detect(cards)
    print(f"  {len(reprint_errata)} reprint errata candidates")

    flagged = {e["card_name"] for e in reprint_errata}

    ocr_errata: list[dict] = []
    if args.no_ocr:
        print("Skipping OCR (--no-ocr).")
    elif not config.have_api_key():
        print("Skipping OCR (no ANTHROPIC_API_KEY in .env).")
    else:
        print("Running OCR detection (Claude vision)…")
        ocr_errata = ocr.detect(cards, flagged, limit=args.ocr_limit)
        print(f"  {len(ocr_errata)} OCR errata candidates")

    if args.download_all:
        print("Downloading all card images…")
        download.download_all()
    elif not args.no_download:
        ids: list[str] = []
        for e in reprint_errata:
            ids += [e["og_id"], e["errata_id"]]
        ids += [e["errata_id"] for e in ocr_errata]
        if ids:
            print(f"Downloading {len(set(ids))} report images…")
            download.download_many(ids)

    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"reprint": reprint_errata, "ocr": ocr_errata}
    config.ERRATA_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {config.ERRATA_JSON}")

    path = report.build(reprint_errata, ocr_errata)
    print(f"Wrote {path}")
    print(
        f"\nDone. {len(reprint_errata)} reprint + {len(ocr_errata)} OCR candidates. "
        f"Open {config.REPORT_HTML} to review."
    )


if __name__ == "__main__":
    main()
