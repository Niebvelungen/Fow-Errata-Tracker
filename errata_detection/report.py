"""Build the HTML errata report.

Layout:
- Reprint cards: original (earliest print) vs errata (latest print).
- OCR cards: the text read from the image is shown as the "original", and the
  stored JSON text is shown as the "OCR Errata" (per review preference).

Review:
- Each card can be flagged "Errata" / "Not errata"; decisions persist in the
  browser via localStorage and can be exported to a JSON file for downstream use.
"""
from __future__ import annotations

import html
import json
import re
from difflib import SequenceMatcher

from . import config
from .loader import load_image_cache


def _word_diff(old: str, new: str) -> tuple[str, str]:
    """Return (old_html, new_html) with word-level deletions/insertions marked."""
    a, b = old.split(), new.split()
    sm = SequenceMatcher(None, a, b)
    old_parts: list[str] = []
    new_parts: list[str] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        old_seg = html.escape(" ".join(a[i1:i2]))
        new_seg = html.escape(" ".join(b[j1:j2]))
        if tag == "equal":
            old_parts.append(old_seg)
            new_parts.append(new_seg)
        elif tag == "delete":
            old_parts.append(f"<del>{old_seg}</del>")
        elif tag == "insert":
            new_parts.append(f"<ins>{new_seg}</ins>")
        elif tag == "replace":
            old_parts.append(f"<del>{old_seg}</del>")
            new_parts.append(f"<ins>{new_seg}</ins>")
    return " ".join(p for p in old_parts if p), " ".join(p for p in new_parts if p)


def _img_tag(card_id: str) -> str:
    """Link the image straight from image_cache.json's URL; 'no image' if absent."""
    url = load_image_cache().get(card_id)
    if not url:
        return '<div class="noimg">no image</div>'
    u = html.escape(url)
    return (
        f'<a href="{u}" target="_blank" rel="noopener">'
        f'<img loading="lazy" src="{u}" alt="{html.escape(card_id)}"></a>'
    )


def _review_bar(key: str) -> str:
    return f"""
      <footer class="review" data-key="{html.escape(key)}">
        <span class="status-dot"></span>
        <button class="rev errata" data-decision="errata">⚑ Errata</button>
        <button class="rev format_change" data-decision="format_change">≈ Format change</button>
        <button class="rev no_change" data-decision="no_change">∅ No change</button>
        <button class="rev clear" data-decision="">Clear</button>
      </footer>"""


def _attrs(cost: str, race: list, changed: list, side: str) -> str:
    """Small cost/race line under a reprint column; highlighted when changed."""
    cost_cls = " chg" if "cost" in changed else ""
    race_cls = " chg" if "race" in changed else ""
    race_str = ", ".join(str(r) for r in race) or "—"
    return (
        f'<div class="attrs">'
        f'<span class="attr{cost_cls}">Cost: {html.escape(cost) or "—"}</span>'
        f'<span class="attr{race_cls}">Race: {html.escape(race_str)}</span>'
        f"</div>"
    )


def _reprint_card(e: dict) -> str:
    old = " ".join(e["og_text"])
    new = " ".join(e["errata_text"])
    old_h, new_h = _word_diff(old, new)
    changed = e.get("changed", ["text"])
    chips = "".join(f'<span class="badge chg">{html.escape(c)}</span>' for c in changed)
    return f"""
    <article class="card reprint" data-key="{html.escape(e['key'])}" data-set="{html.escape(e['set_code'])}">
      <header><h2>{html.escape(e['card_name'])}</h2>
        <span class="badge reprint">reprint</span>{chips}</header>
      <div class="cols">
        <section class="og">
          <div class="meta">Original · {html.escape(e['og_id'])} · {html.escape(e['og_set'])}</div>
          {_img_tag(e['og_id'])}
          <p class="text">{old_h or '<em>(no text)</em>'}</p>
          {_attrs(e.get('og_cost', ''), e.get('og_race', []), changed, 'og')}
        </section>
        <section class="er">
          <div class="meta">Errata · {html.escape(e['errata_id'])} · {html.escape(e['errata_set'])}</div>
          {_img_tag(e['errata_id'])}
          <p class="text">{new_h or '<em>(no text)</em>'}</p>
          {_attrs(e.get('errata_cost', ''), e.get('errata_race', []), changed, 'er')}
        </section>
      </div>
      {_review_bar(e['key'])}
    </article>"""


def _panel(meta_html: str, img_html: str, text_html: str | None) -> str:
    return (
        f'<section><div class="meta">{meta_html}</div>{img_html}'
        f'<p class="text">{text_html or "<em>(none)</em>"}</p></section>'
    )


def _ocr_card(e: dict) -> str:
    json_text = " ".join(e["json_text"])
    badges = []
    for lbl, k in (("old↔new", "sim_old_new"), ("old↔json", "sim_old_json"), ("new↔json", "sim_new_json")):
        if e.get(k) is not None:
            badges.append(f"{lbl} {e[k]}")
    badge = html.escape(" · ".join(badges))

    if e["single"]:
        # One printing: image (OCR) vs stored JSON.
        ocr = e["newest_ocr"] or e["oldest_ocr"] or ""
        ocr_h, json_h = _word_diff(ocr, json_text)
        cols = "cols"
        panels = _panel(
            f"Printed image (OCR) · {html.escape(e['newest_id'])} · {html.escape(e['newest_set'])}",
            _img_tag(e["newest_id"]), ocr_h,
        ) + _panel("Stored JSON text", "", json_h)
    else:
        # Oldest vs newest printed image (the errata), JSON shown for reference.
        oo, no = e["oldest_ocr"], e["newest_ocr"]
        if oo is not None and no is not None:
            old_h, new_h = _word_diff(oo, no)
        else:
            old_h = html.escape(oo) if oo else None
            new_h = html.escape(no) if no else None
        cols = "cols3"
        panels = (
            _panel(
                f"Oldest print (OCR) · {html.escape(e['oldest_id'])} · {html.escape(e['oldest_set'])}",
                _img_tag(e["oldest_id"]), old_h,
            )
            + _panel(
                f"Newest print (OCR) · {html.escape(e['newest_id'])} · {html.escape(e['newest_set'])}",
                _img_tag(e["newest_id"]), new_h,
            )
            + _panel("Stored JSON text", "", html.escape(json_text))
        )

    words = e.get("diff_words") or []
    wchips = "".join(f'<span class="badge chg">{html.escape(w)}</span>' for w in words[:8])
    return f"""
    <article class="card ocr" data-key="{html.escape(e['key'])}" data-set="{html.escape(e['set_code'])}">
      <header><h2>{html.escape(e['card_name'])}</h2>
        <span class="badge ocr">OCR · {badge}</span>{wchips}</header>
      <div class="{cols}">
        {panels}
      </div>
      {_review_bar(e['key'])}
    </article>"""


_CSS = """
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body { font: 15px/1.5 system-ui, sans-serif; margin: 0; background: #f4f4f6; color: #1a1a1a; }
@media (prefers-color-scheme: dark){ body{ background:#15161a; color:#e6e6e6; } }
header.top { padding: 24px 32px; background: #2a2d3a; color: #fff; }
header.top h1 { margin: 0 0 4px; font-size: 22px; }
header.top .sub { opacity: .8; font-size: 14px; }
.toolbar { padding: 10px 32px; position: sticky; top: 0; background: inherit; z-index: 5;
  border-bottom: 1px solid #ccc3; backdrop-filter: blur(6px); display: flex; gap: 18px;
  flex-wrap: wrap; align-items: center; }
.toolbar .group { display: flex; gap: 6px; align-items: center; }
.toolbar .label { font-size: 11px; text-transform: uppercase; letter-spacing: .05em; opacity: .55; }
.toolbar button { font: inherit; font-size: 13px; padding: 5px 12px; border-radius: 16px;
  border: 1px solid #8884; background: #fff2; cursor: pointer; }
.toolbar button.active { background: #4860ff; color: #fff; border-color: #4860ff; }
.toolbar .spacer { flex: 1; }
.toolbar .export { background: #1f9d55; color: #fff; border-color: #1f9d55; font-weight: 600; }
.toolbar .export.blk { background: #6b7280; border-color: #6b7280; }
.toolbar .counts { font-size: 13px; opacity: .75; }
.layout { display: flex; align-items: flex-start; }
nav.sets { position: sticky; top: 52px; flex: 0 0 220px; width: 220px;
  max-height: calc(100vh - 56px); overflow-y: auto; padding: 14px 10px 40px; }
nav.sets .label { font-size: 11px; text-transform: uppercase; letter-spacing: .05em;
  opacity: .55; padding: 4px 10px 8px; }
nav.sets button { display: flex; justify-content: space-between; gap: 8px; align-items: baseline;
  width: 100%; text-align: left; font: inherit; font-size: 13px; padding: 6px 10px; margin: 2px 0;
  border: 1px solid transparent; border-radius: 8px; background: transparent; cursor: pointer;
  color: inherit; }
nav.sets button:hover { background: #00000010; }
@media (prefers-color-scheme: dark){ nav.sets button:hover{ background:#ffffff14; } }
nav.sets button.active { background: #4860ff; color: #fff; }
nav.sets .code { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
nav.sets .n { opacity: .65; font-variant-numeric: tabular-nums; flex: none; }
nav.sets button.active .n { opacity: .9; }
main { flex: 1; min-width: 0; padding: 20px 32px 80px; }
.card { background: #fff; border-radius: 12px; margin: 18px 0; padding: 16px 20px;
  box-shadow: 0 1px 4px #0001; border-left: 5px solid transparent; }
@media (prefers-color-scheme: dark){ .card{ background:#1f2026; box-shadow:none; border:1px solid #ffffff14; border-left:5px solid transparent; } }
.card.decided-errata { border-left-color: #e0483a; }
.card.decided-format_change { border-left-color: #e0a33a; }
.card.decided-no_change { border-left-color: #9aa0aa; opacity: .6; }
.card header { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }
.card h2 { font-size: 18px; margin: 0; }
.badge { font-size: 11px; font-weight: 700; padding: 3px 10px; border-radius: 12px; text-transform: uppercase; letter-spacing: .04em; }
.badge.reprint { background: #e0e7ff; color: #3340b0; }
.badge.ocr { background: #ffe7d1; color: #a4520a; }
.badge.chg { background: #fde7c0; color: #8a5200; }
.attrs { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 8px; }
.attr { font-size: 12px; padding: 3px 8px; border-radius: 6px; background: #00000008; }
@media (prefers-color-scheme: dark){ .attr{ background:#ffffff10; } }
.attr.chg { background: #fde7c0; color: #8a5200; font-weight: 600; }
.cols { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
.cols3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 20px; }
@media (max-width: 720px){ .cols, .cols3 { grid-template-columns: 1fr; } }
@media (min-width: 721px) and (max-width: 1000px){ .cols3 { grid-template-columns: 1fr 1fr; } }
.meta { font-size: 12px; opacity: .7; margin-bottom: 8px; }
.card img { width: 100%; max-width: 260px; border-radius: 8px; display: block; margin-bottom: 10px; }
.noimg { font-size: 12px; opacity: .5; padding: 8px 0; }
.text { background: #fafafa; border: 1px solid #0000000d; border-radius: 8px; padding: 10px 12px; margin: 0; }
@media (prefers-color-scheme: dark){ .text{ background:#0000001f; border-color:#ffffff14; } }
del { background: #ffd7d5; color: #86181d; text-decoration: line-through; }
ins { background: #c9f0d0; color: #11662b; text-decoration: none; }
@media (prefers-color-scheme: dark){ del{ background:#75232855; color:#ffb3ad; } ins{ background:#1d5e3055; color:#9be8ad; } }
.review { display: flex; gap: 8px; align-items: center; margin-top: 14px; padding-top: 12px; border-top: 1px solid #0000000f; }
@media (prefers-color-scheme: dark){ .review{ border-top-color:#ffffff14; } }
.review .status-dot { width: 10px; height: 10px; border-radius: 50%; background: #c9ccd2; }
.card.decided-errata .status-dot { background: #e0483a; }
.card.decided-format_change .status-dot { background: #e0a33a; }
.card.decided-no_change .status-dot { background: #9aa0aa; }
.review .rev { font: inherit; font-size: 13px; padding: 5px 12px; border-radius: 8px;
  border: 1px solid #8884; background: #fff2; cursor: pointer; }
.review .rev.clear { opacity: .6; }
.card.decided-errata .rev.errata { background: #e0483a; color: #fff; border-color: #e0483a; }
.card.decided-format_change .rev.format_change { background: #e0a33a; color: #fff; border-color: #e0a33a; }
.card.decided-no_change .rev.no_change { background: #6b7280; color: #fff; border-color: #6b7280; }
"""

_JS_TEMPLATE = """
const STORE = 'fow-errata-decisions';
const DATA = __DATA__;
const byKey = Object.fromEntries(DATA.map(d => [d.key, d]));

function load() { try { return JSON.parse(localStorage.getItem(STORE) || '{}'); } catch { return {}; } }
function save(obj) { localStorage.setItem(STORE, JSON.stringify(obj)); }
let decisions = load();

const DECISIONS = ['errata', 'format_change', 'no_change'];
function applyCard(article) {
  const d = decisions[article.dataset.key];
  DECISIONS.forEach(x => article.classList.remove('decided-' + x));
  if (DECISIONS.includes(d)) article.classList.add('decided-' + d);
}

let filterSource = 'all', filterStatus = 'all', filterSet = 'all';
function statusOf(article) {
  const d = decisions[article.dataset.key];
  return d || 'unreviewed';
}
function applyFilter() {
  document.querySelectorAll('.card').forEach(c => {
    const okS = filterSource === 'all' || c.classList.contains(filterSource);
    const okT = filterStatus === 'all' || statusOf(c) === filterStatus;
    const okSet = filterSet === 'all' || c.dataset.set === filterSet;
    c.style.display = (okS && okT && okSet) ? '' : 'none';
  });
}

function updateCounts() {
  const c = { errata: 0, format_change: 0, no_change: 0 };
  for (const k in decisions) if (k in byKey && decisions[k] in c) c[decisions[k]]++;
  const total = DATA.length;
  const reviewed = c.errata + c.format_change + c.no_change;
  document.getElementById('counts').textContent =
    `${c.errata} errata · ${c.format_change} format · ${c.no_change} no-change · ` +
    `${total - reviewed} unreviewed of ${total}`;
}

function download(name, obj) {
  const blob = new Blob([JSON.stringify(obj, null, 2)], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  URL.revokeObjectURL(a.href);
}

// Wire review buttons.
document.querySelectorAll('.review').forEach(bar => {
  const article = bar.closest('.card');
  bar.querySelectorAll('.rev').forEach(btn => {
    btn.onclick = () => {
      const dec = btn.dataset.decision;
      if (dec) decisions[bar.dataset.key] = dec;
      else delete decisions[bar.dataset.key];
      save(decisions);
      applyCard(article);
      updateCounts();
      applyFilter();
    };
  });
});

// Wire filter groups.
document.querySelectorAll('.toolbar .group[data-group]').forEach(group => {
  group.querySelectorAll('button').forEach(b => {
    b.onclick = () => {
      group.querySelectorAll('button').forEach(x => x.classList.remove('active'));
      b.classList.add('active');
      if (group.dataset.group === 'source') filterSource = b.dataset.filter;
      else filterStatus = b.dataset.filter;
      applyFilter();
    };
  });
});

// Wire the left set sidebar.
document.querySelectorAll('nav.sets button').forEach(b => {
  b.onclick = () => {
    document.querySelectorAll('nav.sets button').forEach(x => x.classList.remove('active'));
    b.classList.add('active');
    filterSet = b.dataset.set;
    applyFilter();
  };
});

// Export the full categorized dataset (for the future display site).
document.getElementById('export').onclick = () => {
  const withDecision = DATA.map(d => ({ ...d, decision: decisions[d.key] || 'unreviewed' }));
  download('errata-data.json', {
    generated_in_browser: true,
    entries: withDecision,
    errata: withDecision.filter(d => d.decision === 'errata'),
    format_changes: withDecision.filter(d => d.decision === 'format_change'),
    no_change: withDecision.filter(d => d.decision === 'no_change'),
  });
};

// Export blacklist.json — the "No change" keys, re-importable by the detector.
document.getElementById('export-blacklist').onclick = () => {
  const keys = DATA.map(d => d.key).filter(k => decisions[k] === 'no_change');
  download('blacklist.json', { keys });
};

document.querySelectorAll('.card').forEach(applyCard);
updateCounts();
applyFilter();
"""


def _export_entry(e: dict) -> dict:
    """Compact, self-contained record embedded for in-browser export."""
    base = {
        "key": e["key"],
        "card_name": e["card_name"],
        "source": e["source"],
        "set_code": e["set_code"],
    }
    if e["source"] == "reprint":
        base.update(
            {
                "changed": e.get("changed", ["text"]),
                "og_id": e["og_id"],
                "og_set": e["og_set"],
                "og_text": e["og_text"],
                "og_race": e.get("og_race", []),
                "og_cost": e.get("og_cost", ""),
                "errata_id": e["errata_id"],
                "errata_set": e["errata_set"],
                "errata_text": e["errata_text"],
                "errata_race": e.get("errata_race", []),
                "errata_cost": e.get("errata_cost", ""),
            }
        )
    else:
        base.update(
            {
                "errata_id": e["errata_id"],
                "errata_set": e["errata_set"],
                "single": e["single"],
                "alternative": e.get("alternative", False),
                "oldest_id": e["oldest_id"],
                "oldest_set": e["oldest_set"],
                "oldest_ocr": e["oldest_ocr"],
                "newest_id": e["newest_id"],
                "newest_set": e["newest_set"],
                "newest_ocr": e["newest_ocr"],
                "stored_json_text": e["json_text"],
                "diff_words": e.get("diff_words", []),
                "sim_old_new": e.get("sim_old_new"),
                "sim_old_json": e.get("sim_old_json"),
                "sim_new_json": e.get("sim_new_json"),
                "similarity": e["similarity"],
            }
        )
    return base


def build(reprint_errata: list[dict], ocr_errata: list[dict]) -> str:
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Set order, names, and per-card order straight from cards.json structure.
    from .loader import load_cards

    set_rank: dict[str, int] = {}
    set_name: dict[str, str] = {}
    id_order: dict[str, int] = {}
    id_set: dict[str, str] = {}
    for c in load_cards():
        if c.set_code not in set_rank:
            set_rank[c.set_code] = len(set_rank)
            set_name[c.set_code] = c.set_name
        id_order[c.id] = c.order
        id_set[c.id] = c.set_code

    def is_master_piece(code: str) -> bool:
        return bool(re.match(r"^MP\d", code or ""))

    for e in reprint_errata:
        e["key"] = "R:" + e["errata_id"]
    for e in ocr_errata:
        e["key"] = "O:" + e["errata_id"]

    entries: list[tuple[str, dict]] = []
    for kind, lst in (("reprint", reprint_errata), ("ocr", ocr_errata)):
        for e in lst:
            e["set_code"] = id_set.get(e["errata_id"]) or e.get("errata_set", "").split(" — ")[0]
            entries.append((kind, e))

    # Sort by set structure; Master Piece (MP01-03) grouped together at the end.
    entries.sort(
        key=lambda it: (
            is_master_piece(it[1]["set_code"]),
            set_rank.get(it[1]["set_code"], 10**9),
            id_order.get(it[1]["errata_id"], 10**9),
        )
    )

    cards_html = [
        _reprint_card(e) if kind == "reprint" else _ocr_card(e) for kind, e in entries
    ]
    n_re, n_ocr = len(reprint_errata), len(ocr_errata)

    # Left sidebar: one tab per set that has data, in the same sorted order.
    counts: dict[str, int] = {}
    set_order_present: list[str] = []
    for _, e in entries:
        code = e["set_code"]
        if code not in counts:
            set_order_present.append(code)
        counts[code] = counts.get(code, 0) + 1
    nav_items = [
        f'<button class="active" data-set="all"><span class="code">All</span>'
        f'<span class="n">{len(entries)}</span></button>'
    ]
    for code in set_order_present:
        nm = html.escape(set_name.get(code, ""))
        nav_items.append(
            f'<button data-set="{html.escape(code)}" title="{nm}">'
            f'<span class="code">{html.escape(code) or "—"}</span>'
            f'<span class="n">{counts[code]}</span></button>'
        )
    nav_html = "\n      ".join(nav_items)

    data = [_export_entry(e) for _, e in entries]
    # Escape "</" so card text can't terminate the <script> tag (\/ is valid JSON).
    data_json = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    js = _JS_TEMPLATE.replace("__DATA__", data_json)

    doc = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Force of Will — Errata Report</title>
<style>{_CSS}</style></head>
<body>
<header class="top">
  <h1>Force of Will — Errata Report</h1>
  <div class="sub">{n_re + n_ocr} candidates · {n_re} from reprints · {n_ocr} from OCR ·
  <del>deletions</del> / <ins>additions</ins> highlighted · decisions saved in your browser</div>
</header>
<div class="toolbar">
  <div class="group" data-group="source">
    <span class="label">Source</span>
    <button class="active" data-filter="all">All ({n_re + n_ocr})</button>
    <button data-filter="reprint">Reprint ({n_re})</button>
    <button data-filter="ocr">OCR ({n_ocr})</button>
  </div>
  <div class="group" data-group="status">
    <span class="label">Review</span>
    <button class="active" data-filter="all">All</button>
    <button data-filter="errata">Errata</button>
    <button data-filter="format_change">Format change</button>
    <button data-filter="no_change">No change</button>
    <button data-filter="unreviewed">Unreviewed</button>
  </div>
  <div class="spacer"></div>
  <span class="counts" id="counts"></span>
  <button class="export" id="export">⬇ Export data</button>
  <button class="export blk" id="export-blacklist">⬇ blacklist.json</button>
</div>
<div class="layout">
  <nav class="sets">
      <div class="label">Sets</div>
      {nav_html}
  </nav>
  <main>
{''.join(cards_html) or '<p>No errata detected.</p>'}
  </main>
</div>
<script>{js}</script>
</body></html>"""

    config.REPORT_HTML.write_text(doc, encoding="utf-8")
    return str(config.REPORT_HTML)
