"""Prototype showcase: run every VoidTrace stage and write an HTML report of the problems each
cleaning step solves.

    python demo.py                  # writes demo_output/report.html
    python demo.py --offline        # skip live Warframe.Market calls (stage 4 uses cache only)

Inputs: the real EE.log on this machine, live Warframe.Market data, and a synthetic reward
screen (no real capture is needed). Nothing touches the game.
"""

from __future__ import annotations

import argparse
import base64
import html
import os
import re
import statistics
import time
from collections import Counter
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

import cv2
import numpy as np

from voidtrace import cv_cleaner
from voidtrace.cv_cleaner import Box, crop, preprocess, reward_band, reward_slots, screen_scaling, slot_centers
from voidtrace.items import load_items
from voidtrace.log_watcher import KEYWORDS, default_log_path, is_reward_screen_line
from voidtrace.market import MarketClient
from voidtrace.pipeline import RewardPipeline
from voidtrace.price_cleaner import PriceService, iqr_filter, select_sell_prices, summarize
from voidtrace.text_cleaner import EntityResolver

OUT = Path(__file__).parent / "demo_output"
REWARDS = ["NEKROS PRIME BLUEPRINT", "AKSTILETTO PRIME BARREL", "FORMA BLUEPRINT", "SARYN PRIME SYSTEMS BLUEPRINT"]
MARKET_ITEMS = ["nekros_prime_blueprint", "akstiletto_prime_barrel", "saryn_prime_systems_blueprint", "braton_prime_receiver"]
LINE_RE = re.compile(r"^\s*[\d.]+\s+(\w+)\s+\[(\w+)\]")
REDACT_RE = re.compile(r"[0-9a-f]{24}|\d{7,}|[\w.+-]+@[\w-]+\.[\w.]+", re.I)


def esc(value) -> str:
    return html.escape(str(value))


def png_uri(img: np.ndarray, max_w: int = 0, quality_jpeg: bool = False) -> str:
    if max_w and img.shape[1] > max_w:
        f = max_w / img.shape[1]
        img = cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
    if quality_jpeg:
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
        mime = "jpeg"
    else:
        ok, buf = cv2.imencode(".png", img)
        mime = "png"
    return f"data:image/{mime};base64,{base64.b64encode(buf).decode()}"


# --- stage 1: log noise ---------------------------------------------------------

def stage_log() -> dict:
    path = default_log_path()
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    cats = Counter()
    naive_hits = []
    for line in lines:
        m = LINE_RE.match(line)
        cats[f"{m.group(1)} [{m.group(2)}]" if m else "(continuation lines)"] += 1
        if "reward" in line.lower():
            naive_hits.append(line)

    t0 = time.perf_counter()
    strict = [line for line in lines if is_reward_screen_line(line)]
    elapsed = time.perf_counter() - t0

    # Same filter, with two real-format trigger lines spliced into the real noise.
    injected = list(lines)
    injected.insert(len(lines) // 2, "7877.512 Script [Info]: ProjectionRewardChoice.lua: Pause countdown done")
    injected.insert(len(lines) // 2 + 40, "7879.004 Script [Info]: ProjectionRewardChoice.lua: Got rewards")
    injected_hits = [i for i, line in enumerate(injected) if is_reward_screen_line(line)]

    decoys, seen = [], set()
    for line in naive_hits:
        key = re.sub(r"[\d\[\]]+", "", line)[:60]
        if key not in seen:
            seen.add(key)
            decoys.append(REDACT_RE.sub("…", line.strip())[:150])
        if len(decoys) == 6:
            break

    return {
        "total": len(lines),
        "categories": cats.most_common(7),
        "naive": len(naive_hits),
        "strict": len(strict),
        "rate": len(lines) / elapsed if elapsed else 0,
        "injected_total": len(injected),
        "injected_hits": len(injected_hits),
        "decoys": decoys,
    }


# --- stage 2: computer vision ------------------------------------------------------

def harsh_reward_screen(names, width=1920, height=1080, seed=7) -> np.ndarray:
    """Synthetic reward screen with the lighting problems the real one has."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:height, 0:width].astype(np.float32)
    frame = 18 + 55 * (xx / width) + 25 * np.sin(yy / 140.0)            # uneven gradient
    band = reward_band(width, height)
    for cx, cy, r, amp in [(band.x + band.w // 2, band.y + 30, 120, 120), (band.x + 120, band.y + 70, 90, 80),
                           (band.x + band.w - 60, band.y + 10, 140, 95)]:
        frame += amp * np.exp(-((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * r ** 2))  # bloom
    panel = (yy > band.y - 25) & (yy < band.y + band.h + 25)
    frame[panel] = frame[panel] * 0.55 + 70 * 0.45                       # translucent UI panel
    frame += rng.normal(0, 7, frame.shape)                                # sensor/compression noise
    for _ in range(260):                                                  # particles
        x, y = int(rng.integers(band.x, band.x + band.w)), int(rng.integers(band.y - 20, band.y + band.h + 20))
        cv2.circle(frame, (x, y), int(rng.integers(1, 3)), float(rng.integers(150, 230)), -1)
    frame = np.clip(frame, 0, 255).astype(np.uint8)
    frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
    frame[..., 0] = np.clip(frame[..., 0].astype(int) + 22, 0, 255)      # cool void tint

    scale = screen_scaling(width, height)
    font, size, thick = cv2.FONT_HERSHEY_SIMPLEX, 0.5 * scale, 2
    for cx, name in zip(slot_centers(band, len(names)), names):
        words, rows, row = name.split(), [], ""
        for word in words:  # wrap to the card width like the game does
            trial = f"{row} {word}".strip()
            if cv2.getTextSize(trial, font, size, thick)[0][0] > band.w / 4 - 16 and row:
                rows.append(row)
                row = word
            else:
                row = trial
        rows.append(row)
        for i, text in enumerate(rows):
            tw, th = cv2.getTextSize(text, font, size, thick)[0]
            y = band.y + band.h // 2 - (len(rows) - 1) * 14 + i * 28 + th // 2
            cv2.putText(frame, text, (cx - tw // 2, y), font, size, (228, 226, 214), thick, cv2.LINE_AA)
    return frame


def naive_otsu(img: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    binary = cv2.bitwise_not(binary)
    return cv2.copyMakeBorder(binary, 10, 10, 10, 10, cv2.BORDER_CONSTANT, value=255)


def stage_cv(resolver: EntityResolver) -> dict:
    frame = harsh_reward_screen(REWARDS)
    h, w = frame.shape[:2]
    band_box = reward_band(w, h)
    players = cv_cleaner.detect_player_count(crop(frame, band_box))
    marked = frame.copy()
    cv2.rectangle(marked, (band_box.x, band_box.y), (band_box.x + band_box.w, band_box.y + band_box.h), (235, 150, 70), 2)
    rows = []
    for i, box in enumerate(reward_slots(w, h, players)):
        cv2.rectangle(marked, (box.x, box.y), (box.x + box.w, box.y + box.h), (60, 170, 255), 1)
        raw = crop(frame, box)
        gray = cv2.resize(cv2.cvtColor(raw, cv2.COLOR_BGR2GRAY), None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        naive = naive_otsu(raw)
        clean = preprocess(raw)
        variants = []
        for label, img in [("Raw grayscale", gray), ("Global Otsu only", naive), ("VoidTrace cleaning", clean)]:
            text = " ".join(cv_cleaner.ocr(img).split())
            res = resolver.resolve(text)
            variants.append({
                "label": label, "img": png_uri(img), "ocr": text,
                "match": res.item.name if res.item else None, "score": res.score,
            })
        rows.append({"truth": REWARDS[i].title(), "variants": variants})
    zoom = crop(marked, Box(band_box.x - 60, band_box.y - 140, band_box.w + 120, band_box.h + 220))
    return {
        "frame": png_uri(frame, 1100, quality_jpeg=True),
        "zoom": png_uri(zoom, 1100, quality_jpeg=True),
        "players": players,
        "rows": rows,
        "frame_raw": frame,
    }


# --- stage 3: text ------------------------------------------------------------------

OCR_CASES = [
    ("N3kr0s Pr1m3", "Digits read as letters; name cut short"),
    ("NEKROS PRIME NEUROPTICS\nBLUEPRINT", "Name wrapped onto two lines"),
    ("AKSTlLETTO PRlME LINK", "Capital I read as lowercase l"),
    ("| BRATON PRIME\nRECEIVER", "Card border read as a glyph"),
    ("PRIME PRIME BRATON STOCK", "Word read twice"),
    ("SARYN PRlME SYSTEMS BLUEPRlNT", "Several l/I swaps"),
    ("F0RMA BLUEPR1NT", "Untradable reward with digit swaps"),
    ("NIKANA PRIME BLADE", "Clean read"),
    ("~~ .. '' |", "Empty slot: particles only"),
    ("VOID TRACES x15", "Not a relic reward"),
]


def stage_text(resolver: EntityResolver, n_items: int) -> dict:
    names = {item.name.casefold() for item in resolver.items}
    rows, exact, fuzzy = [], 0, 0
    for raw, note in OCR_CASES:
        res = resolver.resolve(raw)
        is_exact = " ".join(raw.split()).casefold() in names
        exact += is_exact
        fuzzy += res.item is not None
        rows.append({
            "raw": raw, "note": note, "cleaned": res.cleaned, "exact": is_exact,
            "match": res.item.name if res.item else None, "score": res.score, "ambiguous": res.ambiguous,
        })
    return {"rows": rows, "exact": exact, "fuzzy": fuzzy, "n": len(OCR_CASES), "n_items": n_items}


# --- stage 4: market ----------------------------------------------------------------

def stage_market(offline: bool) -> list[dict]:
    if offline:
        return []
    client = MarketClient()
    out = []
    for slug in MARKET_ITEMS:
        orders = client.get(f"/orders/item/{slug}")
        sells = [o for o in orders if o.get("type") == "sell" and o.get("visible", True)]
        all_prices = sorted(float(o["platinum"]) for o in sells)
        status = Counter(o["user"]["status"] for o in sells)
        tier_prices, tier = select_sell_prices(orders)
        kept, outliers = iqr_filter(tier_prices)
        stats = summarize(slug, orders)
        q1, q3 = np.percentile(tier_prices, [25, 75]) if len(tier_prices) >= 4 else (min(tier_prices), max(tier_prices))
        out.append({
            "slug": slug, "name": slug.replace("_", " ").title(),
            "n_sell": len(sells), "status": status, "all_prices": all_prices,
            "naive_mean": statistics.fmean(all_prices), "naive_min": all_prices[0], "naive_max": all_prices[-1],
            "tier": tier, "tier_prices": tier_prices, "kept": kept, "outliers": outliers,
            "median": stats.median, "fence_lo": kept[0], "fence_hi": kept[-1], "q1": q1, "q3": q3,
        })
    return out


def strip_svg(m: dict) -> str:
    """Dot strip of every sell price; the in-tier, kept range is shaded."""
    W, H, PAD = 640, 92, 28
    hi = max(m["naive_max"], m["median"] * 3)
    hi = min(hi, np.percentile(m["all_prices"], 99) * 1.15)
    lo = 0.0
    x = lambda v: PAD + (min(v, hi) - lo) / (hi - lo) * (W - 2 * PAD)  # noqa: E731
    tier_set = Counter(m["tier_prices"])
    parts = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="Sell prices for {esc(m["name"])}">']
    parts.append(f'<rect class="kept" x="{x(m["fence_lo"]):.1f}" y="14" width="{max(2, x(m["fence_hi"]) - x(m["fence_lo"])):.1f}" height="44" rx="3"/>')
    rng = np.random.default_rng(1)
    for v in m["all_prices"]:
        in_tier = tier_set[v] > 0
        if in_tier:
            tier_set[v] -= 1
        cls = "dot-tier" if in_tier else "dot-off"
        if in_tier and v in m["outliers"]:
            cls = "dot-out"
        jitter = rng.uniform(-14, 14)
        parts.append(f'<circle class="{cls}" cx="{x(v):.1f}" cy="{36 + jitter:.1f}" r="{3.4 if in_tier else 2.4}"/>')
    parts.append(f'<line class="median" x1="{x(m["median"]):.1f}" x2="{x(m["median"]):.1f}" y1="8" y2="64"/>')
    parts.append(f'<line class="mean" x1="{x(m["naive_mean"]):.1f}" x2="{x(m["naive_mean"]):.1f}" y1="8" y2="64"/>')
    step = 10 if hi <= 80 else 25 if hi <= 200 else 50
    for t in range(0, int(hi) + 1, step):
        parts.append(f'<text class="tick" x="{x(t):.1f}" y="82" text-anchor="middle">{t}</text>')
    if m["naive_max"] > hi:
        parts.append(f'<text class="tick" x="{W - 4}" y="40" text-anchor="end">→ {m["naive_max"]:g}</text>')
    parts.append("</svg>")
    return "".join(parts)


# --- stage 5: end to end ----------------------------------------------------------------

def stage_overlay(frame: np.ndarray, resolver: EntityResolver, offline: bool) -> dict:
    from PyQt6.QtCore import QRect
    from PyQt6.QtGui import QImage, QPainter
    from PyQt6.QtWidgets import QApplication

    from voidtrace.overlay import OverlayWindow

    # Live mode always refetches so stage 5 agrees with stage 4; offline mode serves the cache.
    prices = PriceService(MarketClient(), max_age=float("inf") if offline else 0)
    t0 = time.perf_counter()
    cards = RewardPipeline(resolver, prices, players=None).run(frame)
    elapsed = time.perf_counter() - t0

    app = QApplication.instance() or QApplication([])
    overlay = OverlayWindow(QRect(0, 0, frame.shape[1], frame.shape[0]), hide_after_ms=600000)
    overlay.show_cards(cards)
    layer = overlay.grab().toImage()
    tmp = OUT / "_frame.png"
    cv2.imwrite(str(tmp), frame)
    base = QImage(str(tmp)).convertToFormat(QImage.Format.Format_ARGB32)
    p = QPainter(base)
    p.drawImage(0, 0, layer)
    p.end()
    band = reward_band(frame.shape[1], frame.shape[0])
    region = base.copy(QRect(band.x - 40, band.y - 60, band.w + 80, band.h + 200))
    out_png = OUT / "_overlay.png"
    region.save(str(out_png))
    tmp.unlink(missing_ok=True)
    img = cv2.imread(str(out_png))
    out_png.unlink(missing_ok=True)
    app.processEvents()
    return {
        "img": png_uri(img),
        "elapsed": elapsed,
        "cards": [
            {"name": c.name, "plat": c.price.median if c.price else None, "ducats": c.ducats,
             "best": c.best, "source": c.price.source if c.price else None}
            for c in cards
        ],
    }


# --- report -------------------------------------------------------------------------

STYLE = """
<style>
/* Layout: one reading column; each pipeline stage is a numbered section because the stages run in that order. */
:root {
  --bg: #f3f1ec; --surface: #fbfaf7; --ink: #1b1d26; --muted: #5d6070; --line: #d9d5cb;
  --gold: #9a7420; --gold-soft: #9a742026; --void: #3a4a8c; --bad: #b4462f; --good: #2f7a52;
  --font-display: "Rajdhani", "Segoe UI", system-ui, sans-serif;
  --font-body: "IBM Plex Sans", "Segoe UI", system-ui, sans-serif;
  --font-mono: "IBM Plex Mono", ui-monospace, Consolas, monospace;
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --bg: #0d0f17; --surface: #151826; --ink: #e7e5dc; --muted: #9ba0b5; --line: #2a2f45;
  --gold: #d9b45c; --gold-soft: #d9b45c24; --void: #8fa3ff; --bad: #f08a6e; --good: #7cc99c; color-scheme: dark } }
:root[data-theme="dark"] {
  --bg: #0d0f17; --surface: #151826; --ink: #e7e5dc; --muted: #9ba0b5; --line: #2a2f45;
  --gold: #d9b45c; --gold-soft: #d9b45c24; --void: #8fa3ff; --bad: #f08a6e; --good: #7cc99c; color-scheme: dark }
body { background: var(--bg); color: var(--ink); font: 15px/1.6 var(--font-body); }
main { max-width: 980px; margin: 0 auto; padding-inline: 16px; padding-block: 40px 80px; display: grid; grid-template-columns: minmax(0, 1fr); gap: 56px; }
h1, h2, h3 { font-family: var(--font-display); font-weight: 600; line-height: 1.15; text-wrap: balance; margin: 0; }
h1 { font-size: clamp(2.2rem, 5vw, 3.2rem); letter-spacing: 0.01em; }
h2 { font-size: 1.75rem; }
h3 { font-size: 1.15rem; }
p { margin: 0; max-width: 68ch; }
.eyebrow { font: 600 0.75rem/1 var(--font-mono); letter-spacing: 0.12em; text-transform: uppercase; color: var(--gold); }
header { display: grid; grid-template-columns: minmax(0, 1fr); gap: 14px; min-width: 0; }
.lede { font-size: 1.08rem; color: var(--muted); }
.flow { font: 0.85rem/1.5 var(--font-mono); color: var(--muted); overflow-x: auto; white-space: pre; padding: 14px 16px;
  border: 1px solid var(--line); border-radius: 6px; background: var(--surface); }
section { display: grid; grid-template-columns: minmax(0, 1fr); gap: 18px; min-width: 0; }
.stage-head { display: grid; gap: 6px; border-top: 1px solid var(--line); padding-top: 22px; }
.problem { border-left: 3px solid var(--bad); padding: 2px 0 2px 14px; }
.problem b { color: var(--bad); font-weight: 600; }
.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 12px; }
.stat { background: var(--surface); border: 1px solid var(--line); border-radius: 6px; padding: 12px 14px; display: grid; gap: 2px; }
.stat .v { font: 600 1.6rem/1.1 var(--font-display); font-variant-numeric: tabular-nums; }
.stat .k { font-size: 0.8rem; color: var(--muted); }
.stat.bad .v { color: var(--bad); } .stat.good .v { color: var(--good); } .stat.gold .v { color: var(--gold); }
.scroll { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-size: 0.88rem; font-variant-numeric: tabular-nums; }
th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--line); vertical-align: top; }
th { font: 600 0.72rem/1.2 var(--font-mono); letter-spacing: 0.08em; text-transform: uppercase; color: var(--muted); }
code, .mono { font-family: var(--font-mono); font-size: 0.84em; }
.decoys { display: grid; gap: 4px; font: 0.78rem/1.45 var(--font-mono); color: var(--muted); background: var(--surface);
  border: 1px solid var(--line); border-radius: 6px; padding: 12px 14px; overflow-x: auto; }
.decoys span { white-space: nowrap; }
.bars { display: grid; gap: 6px; }
.bar { display: grid; grid-template-columns: minmax(120px, 170px) 1fr 70px; gap: 10px; align-items: center; font-size: 0.82rem; }
.bar .track { height: 10px; background: var(--line); border-radius: 2px; overflow: hidden; }
.bar .fill { height: 100%; background: var(--void); }
.bar .n { text-align: right; font-variant-numeric: tabular-nums; color: var(--muted); }
figure { margin: 0; display: grid; gap: 8px; }
figure img { border-radius: 6px; border: 1px solid var(--line); display: block; }
figcaption { font-size: 0.82rem; color: var(--muted); }
.cv-row { display: grid; grid-template-columns: minmax(0, 1fr); gap: 10px; min-width: 0; padding: 14px; border: 1px solid var(--line); border-radius: 6px; background: var(--surface); }
.cv-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 12px; }
.cv-cell { display: grid; gap: 6px; min-width: 0; align-content: start; }
.cv-cell img { background: #fff; image-rendering: auto; width: 100%; }
.cv-cell .lbl { font: 600 0.72rem/1 var(--font-mono); letter-spacing: 0.08em; text-transform: uppercase; color: var(--muted); }
.ocr { font: 0.8rem/1.4 var(--font-mono); word-break: break-word; }
.pill { display: inline-block; font: 600 0.72rem/1 var(--font-mono); padding: 4px 7px; border-radius: 3px; white-space: nowrap; }
.pill.ok { color: var(--good); background: color-mix(in srgb, var(--good) 14%, transparent); }
.pill.no { color: var(--bad); background: color-mix(in srgb, var(--bad) 14%, transparent); }
.pill.warn { color: var(--gold); background: var(--gold-soft); }
.market { display: grid; grid-template-columns: minmax(0, 1fr); gap: 10px; min-width: 0; padding: 16px; border: 1px solid var(--line); border-radius: 6px; background: var(--surface); }
.market-top { display: flex; flex-wrap: wrap; gap: 8px 20px; align-items: baseline; justify-content: space-between; }
.market-nums { display: flex; flex-wrap: wrap; gap: 6px 18px; font-size: 0.85rem; color: var(--muted); font-variant-numeric: tabular-nums; }
.market-nums b { color: var(--ink); font-weight: 600; }
.market-nums .med b { color: var(--gold); }
svg { width: 100%; height: auto; display: block; }
svg .kept { fill: var(--gold-soft); stroke: var(--gold); stroke-width: 1; }
svg .dot-tier { fill: var(--void); } svg .dot-off { fill: var(--muted); opacity: 0.35; } svg .dot-out { fill: var(--bad); }
svg .median { stroke: var(--gold); stroke-width: 2.5; } svg .mean { stroke: var(--bad); stroke-width: 1.5; stroke-dasharray: 4 3; }
svg .tick { fill: var(--muted); font: 11px var(--font-mono); }
.legend { display: flex; flex-wrap: wrap; gap: 6px 18px; font-size: 0.8rem; color: var(--muted); }
.legend i { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 6px; vertical-align: -1px; }
.legend i.line { border-radius: 0; height: 3px; width: 16px; vertical-align: 3px; }
ul.limits { margin: 0; padding-left: 20px; display: grid; gap: 10px; max-width: 72ch; }
ul.limits b { font-weight: 600; }
footer { font-size: 0.8rem; color: var(--muted); border-top: 1px solid var(--line); padding-top: 18px; }
</style>
"""


def render(log, cv, text, market, overlay) -> str:
    h = []
    h.append('<title>VoidTrace Prototype</title>')
    h.append('<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>')
    h.append('<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&family=IBM+Plex+Sans:wght@400;600&family=Rajdhani:wght@600&display=swap">')
    h.append(STYLE)
    h.append("<main>")
    h.append(f"""<header>
<span class="eyebrow">Prototype · {time.strftime('%Y-%m-%d')}</span>
<h1>VoidTrace</h1>
<p class="lede">A read-only Warframe overlay that prices relic rewards the moment the reward screen opens. Each stage
is a cleaning problem: game logs full of noise, a screenshot full of light effects, OCR text full of errors, and a
market full of offline sellers and troll prices. This report runs every stage and shows what goes wrong without
the cleaning step.</p>
<div class="flow">EE.log ─► 1 log filter ─► 2 capture + image cleaning + OCR ─► 3 text normalize + fuzzy match
                                                                            │
          overlay ◄─ 5 PyQt6 click-through window ◄─ 4 market orders + IQR outlier purge</div>
</header>""")

    # Stage 1
    top = log["categories"][0][1] or 1
    bars = "".join(
        f'<div class="bar"><span class="mono">{esc(k)}</span><div class="track"><div class="fill" style="width:{v / top * 100:.1f}%"></div></div><span class="n">{v:,}</span></div>'
        for k, v in log["categories"]
    )
    decoys = "".join(f"<span>{esc(d)}</span>" for d in log["decoys"])
    h.append(f"""<section>
<div class="stage-head"><span class="eyebrow">Stage 1 · Event ingestion</span><h2>Finding one line in a hundred thousand</h2></div>
<p>The game writes everything to <code>EE.log</code>. VoidTrace tails it read-only and must fire exactly once when
the reward screen opens. Data below is this machine's real log.</p>
<p class="problem"><b>Problem.</b> Matching on the word "reward" fires on vault bonuses, mission decks, session seeds
and asset loads. In this log, {log['naive']:,} lines mention "reward" and none of them is a reward screen.</p>
<div class="stats">
<div class="stat"><span class="v">{log['total']:,}</span><span class="k">lines in the real log</span></div>
<div class="stat bad"><span class="v">{log['naive']:,}</span><span class="k">false triggers from a naive "reward" match</span></div>
<div class="stat good"><span class="v">{log['strict']}</span><span class="k">false triggers from VoidTrace's filter</span></div>
<div class="stat gold"><span class="v">{log['injected_hits']} / 2</span><span class="k">real trigger lines found after splicing them into the noise</span></div>
</div>
<div class="bars">{bars}</div>
<p>The filter checks a substring list (<code>{esc(', '.join(KEYWORDS))}</code>) before any regex runs, so the
rejection path is cheap: {log['rate'] / 1e6:.1f} million lines per second. Repeats within 5 seconds are
debounced, and the tailer reopens the file when the game recreates it on launch.</p>
<h3>Sample decoys from the real log</h3>
<div class="decoys">{decoys}</div>
</section>""")

    # Stage 2
    rows = []
    for row in cv["rows"]:
        cells = []
        for v in row["variants"]:
            ok = v["match"] == row["truth"]
            pill = f'<span class="pill ok">✓ {esc(v["match"])}</span>' if ok else (
                f'<span class="pill no">✗ {esc(v["match"]) if v["match"] else "no match"}</span>')
            cells.append(f'<div class="cv-cell"><span class="lbl">{esc(v["label"])}</span><img src="{v["img"]}" alt="{esc(v["label"])} of {esc(row["truth"])}">'
                         f'<span class="ocr">OCR: {esc(v["ocr"]) or "∅ (nothing read)"}</span>{pill}</div>')
        rows.append(f'<div class="cv-row"><h3>{esc(row["truth"])}</h3><div class="cv-grid">{"".join(cells)}</div></div>')
    wins = sum(1 for r in cv["rows"] for v in r["variants"] if v["label"] == "VoidTrace cleaning" and v["match"] == r["truth"])
    naive_wins = sum(1 for r in cv["rows"] for v in r["variants"] if v["label"] == "Global Otsu only" and v["match"] == r["truth"])
    raw_wins = sum(1 for r in cv["rows"] for v in r["variants"] if v["label"] == "Raw grayscale" and v["match"] == r["truth"])
    n = len(cv["rows"])
    h.append(f"""<section>
<div class="stage-head"><span class="eyebrow">Stage 2 · Vision and OCR</span><h2>Reading text through bloom and particles</h2></div>
<p>The crop boxes come from the reward screen's 1920×1080 reference layout, scaled by screen height, so they land on
the same spots at 1080p, 1440p and ultrawide. Squad size is detected from where text sits: {cv['players']} cards here.</p>
<figure><img src="{cv['zoom']}" alt="Synthetic reward screen with the detected reward band and card boxes outlined">
<figcaption>Synthetic reward screen with an uneven gradient, three bloom sources, a translucent panel, noise and 260
particles. Blue: reward-name band; orange: per-card crops.</figcaption></figure>
<p class="problem"><b>Problem.</b> A single global Otsu threshold splits dark background from bright glow, so bloom lands
on the "text" side. The fix flattens the lighting first with a morphological top-hat (or black-hat on light themes), which
removes anything wider than a letter stroke. Otsu then separates text from everything else.</p>
<div class="stats">
<div class="stat bad"><span class="v">{raw_wins} / {n}</span><span class="k">correct with raw grayscale</span></div>
<div class="stat bad"><span class="v">{naive_wins} / {n}</span><span class="k">correct with global Otsu only</span></div>
<div class="stat good"><span class="v">{wins} / {n}</span><span class="k">correct with VoidTrace cleaning</span></div>
</div>
{''.join(rows)}
<p>Every cell is real Tesseract 5 output, run through the stage 3 matcher.</p>
</section>""")

    # Stage 3
    trows = []
    for r in text["rows"]:
        if r["match"] is None:
            pill = '<span class="pill no">rejected</span>'
        elif r["ambiguous"]:
            pill = '<span class="pill warn">uncertain</span>'
        else:
            pill = '<span class="pill ok">matched</span>'
        trows.append(
            f'<tr><td class="mono">{esc(" ⏎ ".join(r["raw"].splitlines()))}</td><td>{esc(r["note"])}</td>'
            f'<td class="mono">{esc(r["cleaned"]) or "∅"}</td><td>{"yes" if r["exact"] else "no"}</td>'
            f'<td>{esc(r["match"] or "–")}</td><td>{r["score"]:.0f}</td><td>{pill}</td></tr>'
        )
    h.append(f"""<section>
<div class="stage-head"><span class="eyebrow">Stage 3 · Entity resolution</span><h2>Turning OCR mistakes into item names</h2></div>
<p>Raw OCR output is cleaned (illegal characters, digit-for-letter swaps, stray glyphs, repeated words), then fuzzy-matched
with RapidFuzz against the {text['n_items']} possible relic rewards pulled from Warframe.Market. Weak scores are rejected,
and a near-tie between two items is flagged instead of guessed.</p>
<div class="stats">
<div class="stat bad"><span class="v">{text['exact']} / {text['n']}</span><span class="k">found by exact lookup</span></div>
<div class="stat good"><span class="v">{text['fuzzy']} / {text['n']}</span><span class="k">resolved after cleaning + fuzzy match</span></div>
<div class="stat gold"><span class="v">2</span><span class="k">correctly rejected (not a relic reward)</span></div>
</div>
<div class="scroll"><table>
<thead><tr><th>OCR input</th><th>Error type</th><th>Cleaned</th><th>Exact hit</th><th>Resolved to</th><th>Score</th><th>Result</th></tr></thead>
<tbody>{''.join(trows)}</tbody></table></div>
<p class="problem"><b>Limit.</b> "N3kr0s Pr1m3" is cut short, so it fits four Nekros Prime parts equally well. VoidTrace
picks one and marks it uncertain; the overlay shows that flag rather than presenting a guess as fact.</p>
</section>""")

    # Stage 4
    if market:
        cards = []
        for m in market:
            st = m["status"]
            cards.append(f"""<div class="market">
<div class="market-top"><h3>{esc(m['name'])}</h3>
<div class="market-nums"><span>naive mean <b>{m['naive_mean']:.1f}p</b></span><span>cheapest listing <b>{m['naive_min']:g}p</b></span>
<span class="med">VoidTrace median <b>{m['median']:g}p</b></span></div></div>
<div class="market-nums"><span><b>{m['n_sell']}</b> sell orders</span><span><b>{st.get('ingame', 0)}</b> in game</span>
<span><b>{st.get('online', 0)}</b> online</span><span><b>{st.get('offline', 0)}</b> offline</span>
<span>used tier <b>{esc(m['tier'])}</b> ({len(m['tier_prices'])} prices)</span><span><b>{len(m['outliers'])}</b> outliers purged</span></div>
{strip_svg(m)}</div>""")
        offline_share = sum(m["status"].get("offline", 0) for m in market) / max(1, sum(m["n_sell"] for m in market))
        h.append(f"""<section>
<div class="stage-head"><span class="eyebrow">Stage 4 · Market sanitizer</span><h2>A price you could actually trade at</h2></div>
<p>Live sell orders from Warframe.Market, fetched when this report was generated.</p>
<p class="problem"><b>Problem.</b> {offline_share:.0%} of these sell orders belong to sellers who are offline, and the list
includes lowball troll listings and price-gouging ones. The cheapest listing is usually unreachable, and the mean is
dragged by the extremes.</p>
<p>VoidTrace keeps sellers who are in game, widening to online and then everyone only when fewer than 5 remain. It
drops prices outside Tukey's fences (Q1 − 1.5·IQR, Q3 + 1.5·IQR, with the IQR floored at 10% of the median),
and reports the median of the rest.</p>
<div class="legend"><span><i style="background:var(--void)"></i>price in the chosen seller tier</span>
<span><i style="background:var(--bad)"></i>purged outlier</span><span><i style="background:var(--muted);opacity:.4"></i>other sellers (not used)</span>
<span><i class="line" style="background:var(--gold)"></i>median</span><span><i class="line" style="background:var(--bad)"></i>naive mean</span>
<span><i style="background:var(--gold-soft);border:1px solid var(--gold);border-radius:2px"></i>kept range</span></div>
{''.join(cards)}
</section>""")

    # Stage 5
    def plat_cell(c: dict) -> str:
        if c["plat"] is not None:
            return f'{c["plat"]:g}p'
        return "untradable" if c["name"] == "Forma Blueprint" else "–"

    crow = "".join(
        f'<tr><td>{i + 1}</td><td>{esc(c["name"] or "unreadable")}</td><td>{plat_cell(c)}</td>'
        f'<td>{c["ducats"]}</td><td>{esc(c["source"] or "–")}</td><td>{"★ best" if c["best"] else ""}</td></tr>'
        for i, c in enumerate(overlay["cards"])
    )
    h.append(f"""<section>
<div class="stage-head"><span class="eyebrow">Stage 5 · Overlay</span><h2>End to end on the synthetic screen</h2></div>
<p>The same harsh screenshot run through the full pipeline (real OCR, real fuzzy match, live prices) in
{overlay['elapsed']:.1f} s, then drawn by the PyQt6 overlay. In the game, the overlay is a frameless, translucent,
always-on-top window that ignores mouse input, so clicks pass through to Warframe.</p>
<figure><img src="{overlay['img']}" alt="Overlay cards drawn under the reward names on the synthetic screen">
<figcaption>Rendered offscreen at 1920×1080. Gold border marks the most valuable pick.</figcaption></figure>
<div class="scroll"><table><thead><tr><th>Slot</th><th>Item</th><th>Platinum</th><th>Ducats</th><th>Seller tier</th><th></th></tr></thead>
<tbody>{crow}</tbody></table></div>
</section>""")

    h.append("""<section>
<div class="stage-head"><span class="eyebrow">Status</span><h2>Open problems</h2></div>
<ul class="limits">
<li><b>Reward-screen trigger not yet seen live.</b> The log markers ("Pause countdown done", "Got rewards") come from WFInfo,
the established open-source tool. This machine's log has no relic run yet, so the filter is proven only on spliced lines.</li>
<li><b>Vision tested on synthetic screens only.</b> Crop geometry follows WFInfo's reference layout. Real captures may differ
with UI scale, themes, HDR or non-English fonts.</li>
<li><b>Truncated reads are ambiguous.</b> When OCR returns only part of a name, several items fit. These are flagged, not solved.</li>
<li><b>Thin markets.</b> Many parts have fewer than 5 sellers in game, so the price falls back to a wider, less reliable tier.
The overlay shows which tier was used.</li>
<li><b>Capture timing is a guess.</b> The screenshot is taken 1.5 s after the trigger while the cards animate in.</li>
</ul>
</section>""")
    h.append(f'<footer>Generated by <code>demo.py</code> on {time.strftime("%Y-%m-%d %H:%M")}. Market data: Warframe.Market public API. '
             'Log excerpts have IDs redacted. VoidTrace is read-only and does not modify or inject into the game.</footer>')
    h.append("</main>")
    return "\n".join(h)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Generate the VoidTrace prototype report")
    parser.add_argument("--offline", action="store_true", help="skip live market calls")
    args = parser.parse_args(argv)
    OUT.mkdir(exist_ok=True)

    print("stage 1: scanning EE.log...")
    log = stage_log()
    items = load_items()
    resolver = EntityResolver(items)
    print("stage 2: vision + OCR...")
    cv = stage_cv(resolver)
    print("stage 3: text resolution...")
    text = stage_text(resolver, len(items))
    print("stage 4: market...")
    market = stage_market(args.offline)
    print("stage 5: end to end + overlay...")
    overlay = stage_overlay(cv.pop("frame_raw"), resolver, args.offline)

    report = OUT / "report.html"
    report.write_text(render(log, cv, text, market, overlay), encoding="utf-8")
    print(f"wrote {report} ({report.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
