# Force of Will — Errata Detection

Detects card errata in the Force of Will TCG two ways and produces an HTML report
showing the previous text vs the errata text side by side, with images.

1. **Reprint diff** — a card name reprinted in a later set whose rules text no
   longer matches the original. `cards.json` is in chronological order, so the
   earliest printing is the original and the latest is the errata. Text is run
   through a heuristic normalizer (`errata_detection/normalize.py`) that collapses
   the game's many template/wording rewrites, so the flagged list is real changes
   rather than ~1000 reformatting diffs.
2. **OCR diff** — for the latest printing of each name *not* already flagged by a
   reprint, Claude vision reads the printed rules text from the card image and
   compares it to the stored JSON text. Cards without an image are skipped. XR
   ("Extension Rule") cards have rotated full-card text; the OCR prompt is told
   the rarity so it reads them correctly. Printed symbol icons (`{W}`, `{Rest}`,
   …) and `{...}` placeholders in the JSON are ignored on both sides.

## Setup

```sh
pip install -r requirements.txt
```

Put your Anthropic API key in `.env` (already created; only needed for OCR):

```
ANTHROPIC_API_KEY=sk-ant-...
```

## Run

```sh
python run.py                  # reprint detect + OCR (if key) + download + report
python run.py --no-ocr         # reprint detection only (no API key needed)
python run.py --ocr-limit 50   # cap OCR to 50 cards (cost control / smoke test)
python run.py --download-all    # also download every card image
python run.py --no-download     # skip image downloads
```

Outputs:

- `output/errata.json` — all candidates (reprint + OCR) as structured data.
- `output/report.html` — reviewable report. Filter by **set** (left sidebar),
  **source**, and **review status**; word-level `deletions` / `additions`
  highlighted. Each card can be categorized **Errata / Format change / No change**;
  decisions persist in the browser (localStorage).

## Review workflow & blacklist

In the report, flag each candidate as **Errata**, **Format change**, or **No
change**, then use the toolbar exports:

- **⬇ Export data** → `errata-data.json` — every entry with its decision, plus
  pre-split `errata` / `format_changes` / `no_change` arrays. This is the curated
  dataset for the future read-only display site.
- **⬇ blacklist.json** → the keys you marked **No change**.

To stop seeing the "No change" entries: drop the downloaded `blacklist.json` in
the project root and re-run. The detector reads it (`config.load_blacklist()`)
and excludes those entries from both the reprint and OCR passes. Keys are
`R:<latest_id>` (reprint) and `O:<card_id>` (OCR). To grow the blacklist over
time, merge new exports into the existing file's `keys` array.
- `images/` — downloaded card images (resumable; existing files skipped).
- `.ocr_cache.json` — cached OCR text per card id, so re-runs don't re-call the API.

## Layout

| File | Role |
|------|------|
| `errata_detection/loader.py` | Load + flatten `cards.json` chronologically; group by name |
| `errata_detection/normalize.py` | Heuristic rules-text normalization (tune patterns here) |
| `errata_detection/reprint.py` | Reprint-based errata detection |
| `errata_detection/ocr.py` | Claude vision OCR + similarity comparison |
| `errata_detection/download.py` | Image downloader (concurrent, resumable) |
| `errata_detection/report.py` | HTML report with side-by-side word diff |
| `run.py` | Pipeline entry point |

## Tuning

- **Reprint false positives** → add patterns to `_REPLACEMENTS` in `normalize.py`.
- **OCR sensitivity** → `OCR_SIMILARITY_THRESHOLD` in `config.py` (lower = fewer,
  higher-confidence flags).
- **OCR model / cost** → defaults to `claude-sonnet-4-6`. Set `OCR_MODEL` in `.env`
  (e.g. `OCR_MODEL=claude-haiku-4-5`) for a cheaper pass. Cached OCR results in
  `.ocr_cache.json` are reused regardless of model; delete it to re-OCR with a
  different model.
