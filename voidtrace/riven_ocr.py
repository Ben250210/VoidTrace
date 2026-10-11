"""Read a riven off the screen (the in-game Item Details view) into a RivenDraft.

The card sits in the middle of the screen at a fixed size, so three crops are enough:
  * text   - weapon + riven name (one or two lines), then one line per stat
  * bar    - "MR 12" on the left, the rolls icon + count on the right (no count when never rolled)
  * drain  - the capacity number top right: 10 at rank 0, 18 at rank 8, so rank = drain - 10

The card's text panel is plain dark, so a plain Otsu threshold on a 4x upscale reads better than
the reward-screen cleaning (which flattens a busy 3D background): it keeps decimal points and %
signs that the morphological filter erased ("170 4/7" instead of "170.4%"). The riven name then
pins down the buffs (buffs_from_name), so a misread stat label cannot pick the wrong stat.

Nothing is posted from a read: it only fills in the price-check form for the user to check.
Tuned on 7 real cards at 2560x1600 (tests/fixtures/rivens/screens); other resolutions unverified.
"""

from __future__ import annotations

import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Optional

import cv2
import numpy as np
from rapidfuzz import fuzz, process

from .cv_cleaner import Box, crop, ocr, screen_scaling
from .rivens import (
    RivenAttr,
    RivenCatalog,
    RivenDraft,
    RivenWeapon,
    buffs_from_name,
    expected_value,
    shown_value,
)
from .text_cleaner import match_score

# Crops in 1920x1080 reference units, relative to the screen centre: (x, y, w, h).
TEXT_BOX = (-110, -14, 220, 140)
BAR_BOX = (-92, 126, 184, 24)
DRAIN_BOX = (66, -156, 36, 26)
POLARITY_BOX = (100, -156, 22, 26)  # the symbol right of the drain number
UPSCALE = 4  # at 2560x1600 (scale 4/3); smaller screens get more, so the text always ends up one size
TUNED_SCALE = 2560 / 1920
BASE_DRAIN = 10  # a rank-0 riven costs 10, +1 per rank
# One OCR pass is fragile: changing the upscale from 4.0 to 4.002 flipped two of seven cards
# ("+174.7%" read as "174.0", "-22%" as "-722"). Each card is read at these multiples of the base
# upscale and every field takes the majority answer (ties go to the first, the base read).
VOTE_FACTORS = (1.0, 0.85, 1.15, 1.3)
WEAPON_MIN = 75  # weapon-name match score needed to trust the title

# Card labels that differ from Warframe.Market's stat names.
LABEL_ALIASES = {
    "fire_rate_/_attack_speed": ("Fire Rate", "Attack Speed"),
    "base_damage_/_melee_damage": ("Damage", "Melee Damage"),
    "recoil": ("Weapon Recoil", "Recoil"),
}
# One stat line: sign or "x", number, optional %, label. The sign is optional because OCR
# sometimes drops a minus; the riven name then decides which stat is the curse.
STAT_LINE = re.compile(r"(?P<sign>[+\-−–xX×])?\s?(?P<num>\d{1,4}(?:[.,]\d{1,3})?)\s*(?P<pct>%?)\s*(?P<label>.*)")


@dataclass
class RivenRead:
    draft: RivenDraft
    warnings: list[str] = field(default_factory=list)
    raw: str = ""
    alternates: dict = field(default_factory=dict)  # slug -> other readings of its number

    @property
    def ok(self) -> bool:
        return bool(self.draft.weapon and self.draft.attrs)


# --- image ----------------------------------------------------------------------------

def ref_box(frame: np.ndarray, box: tuple[int, int, int, int], ui_scale: float = 1.0) -> Box:
    h, w = frame.shape[:2]
    s = screen_scaling(w, h) * ui_scale
    x, y, bw, bh = box
    return Box(int(w / 2 + x * s), int(h / 2 + y * s), int(bw * s), int(bh * s))


def upscale_for(frame: np.ndarray, ui_scale: float = 1.0) -> float:
    """Enlarge crops so letters reach the size they have on the 2560x1600 cards the reader was tuned on.
    A fixed 4x read 7/7 cards at 1920x1200 but 2/7 at 1680x1050 (decimals and minus signs blurred)."""
    h, w = frame.shape[:2]
    return UPSCALE * TUNED_SCALE / (screen_scaling(w, h) * ui_scale)


def binarize(img: np.ndarray, upscale: float = UPSCALE) -> np.ndarray:
    """Light text on the dark card -> black text on white, enlarged."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    gray = cv2.resize(gray, None, fx=upscale, fy=upscale, interpolation=cv2.INTER_CUBIC)
    _, b = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return cv2.copyMakeBorder(255 - b, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=255)


def _glyph_groups(binary: np.ndarray) -> list[list[tuple[int, int, int, int]]]:
    """Letter-sized blobs of a one-line crop, grouped into words by the gaps between them."""
    n, _, stats, _ = cv2.connectedComponentsWithStats(255 - binary, connectivity=8)
    h, w = binary.shape
    boxes = [tuple(stats[i, :4]) for i in range(1, n)
             if stats[i, 3] < 0.9 * h and stats[i, 2] < 0.3 * w and stats[i, 3] > 0.25 * h]
    if not boxes:
        return []
    tall = max(b[3] for b in boxes)
    boxes = sorted((b for b in boxes if b[3] >= 0.6 * tall), key=lambda b: b[0])
    groups = [[boxes[0]]]
    for b in boxes[1:]:
        prev = groups[-1][-1]
        if b[0] - (prev[0] + prev[2]) > 1.2 * tall:  # a gap wider than a letter is tall: new word
            groups.append([b])
        else:
            groups[-1].append(b)
    return groups


def read_bar(binary: np.ndarray) -> tuple[Optional[int], Optional[int]]:
    """(MR, rolls) from the bar. The rolls icon reads as a "0" or "9", so it is counted as a
    glyph and dropped: the rolls number has one digit less than the right-hand group."""
    text = ocr(binary, psm=7)
    # "MR1O", "MRI16": O/I/l are 0/1 misreads; MR tops out at 30 (Legendary 5), so keep the last
    # two digits of anything bigger.
    mr_match = re.search(r"MR\s*([0-9OIl|]{1,3})", text)
    mr = None
    if mr_match:
        digits = mr_match.group(1).translate(str.maketrans("OIl|", "0111"))
        mr = int(digits) if int(digits) <= 30 else int(digits[-2:])
    rolls = 0
    groups = _glyph_groups(binary)
    if len(groups) >= 2 and len(groups[-1]) >= 2:
        digits = re.findall(r"\d+", text[mr_match.end():] if mr_match else text)
        if digits:
            run = digits[-1]
            rolls = int(run[-(len(groups[-1]) - 1):]) if len(run) >= len(groups[-1]) - 1 else int(run)
    return mr, rolls


def read_polarity(img: np.ndarray, upscale: float) -> Optional[str]:
    """Madurai, Vazarin or Naramon (the only riven polarities) from the symbol's shape.

    On the 8 real cards: Naramon is a flat bar (about 2.4x wider than tall); Vazarin a filled
    triangle, solidity (ink / convex hull) 0.78-0.82; Madurai an open V, solidity 0.60-0.67. At some
    sizes the V's two arms come apart, so all symbol pieces are measured together.
    """
    big = cv2.resize(img, None, fx=upscale, fy=upscale, interpolation=cv2.INTER_CUBIC)
    # The symbol is pale lavender; Otsu split Sobek's bright orange art from the dark instead.
    hsv = cv2.cvtColor(big, cv2.COLOR_BGR2HSV)
    b = ((hsv[..., 2] > 150) & (hsv[..., 1] < 110)).astype(np.uint8) * 255
    n, labels, stats, _ = cv2.connectedComponentsWithStats(b, connectivity=8)
    h, w = b.shape
    # The card frame's edge runs across the whole crop; specks are tiny. What is left is the symbol.
    parts = [i for i in range(1, n) if stats[i, 2] < 0.8 * w and stats[i, 3] > 0.15 * h and stats[i, 4] > 0.01 * h * w]
    if not parts:
        return None
    # The biggest piece, plus pieces touching its box (the V's other arm), not frame bits further off.
    main = max(parts, key=lambda i: stats[i, 4])
    mx, my, mw, mh = stats[main, :4]
    pad = 0.15 * max(mw, mh)
    near = [i for i in parts if stats[i, 0] < mx + mw + pad and stats[i, 0] + stats[i, 2] > mx - pad
            and stats[i, 1] < my + mh + pad and stats[i, 1] + stats[i, 3] > my - pad]
    mask = np.isin(labels, near).astype(np.uint8)
    points = cv2.findNonZero(mask)
    x, y, gw, gh = cv2.boundingRect(points)
    if gw / gh > 1.6:
        return "naramon"
    solidity = mask.sum() / max(1.0, cv2.contourArea(cv2.convexHull(points)))
    return "vazarin" if solidity > 0.72 else "madurai"


def read_drain(binary: np.ndarray) -> Optional[int]:
    found = re.search(r"1[0-8]", ocr(binary, psm=7))
    return int(found.group()) if found else None


# --- text -----------------------------------------------------------------------------

def _clean(line: str) -> str:
    line = re.sub(r"(?<=\d)/(?=\d)", "", line)  # "+1/74.7%" -> "+174.7%"
    return line.replace("—", "-").strip()


def _stat_labels(catalog: RivenCatalog, weapon: Optional[RivenWeapon]) -> dict[str, str]:
    """Label -> stat slug, for the stats this weapon can roll."""
    labels = {}
    for stat in (catalog.stats_for(weapon) if weapon else catalog.stats):
        for label in (stat.name, *LABEL_ALIASES.get(stat.slug, ())):
            labels[label.lower()] = stat.slug
    return labels


def _label_of(text: str) -> str:
    text = re.sub(r"\(.*", "", text)              # "Fire Rate (x2 for Bows)"
    words = [w for w in re.findall(r"[A-Za-z]+", text)]
    if words and len(words[0]) <= 2 and len(words) > 1:  # element icons read as "A", "*", "%"
        words = words[1:]
    if words and words[0][:1].isupper() and words[0][1:2].isupper():
        words[0] = words[0][1:]                   # "AHeat": icon glued to the word
    return " ".join(words)


# Tesseract reads this font's "7" as "/": "+174.7%" came back as "+1/4./%" (live, 14:56) and as
# "+1/74.7/%" (a stray extra "/"). So each number is also tried with every "/" read as a 7 and with
# every "/" dropped; the riven's possible value range then decides (read_riven).
NUMBER = re.compile(r"\d{1,4}(?:[.,]\d{1,3})?")


def number_variants(line: str) -> list[float]:
    """Other ways to read the number at the start of a stat line."""
    m = re.search(r"[+\-\u2212\u2013xX\u00d7]?\s?\d", line)
    if not m:
        return []
    start = m.start() + (1 if line[m.start()] in "xX×" else 0)  # "x1.47": the x is the sign, not a letter
    span = re.match(r"[^A-Za-z]*", line[start:]).group()
    found = []
    for variant in (span.replace("/", "7"), span.replace("/", ""), span.replace("/7", "7").replace("/", "7")):
        n = NUMBER.search(variant.lstrip("+-\u2212\u2013xX\u00d7 "))
        if n:
            found.append(float(n.group().replace(",", ".")))
    return list(dict.fromkeys(found))


def parse_stat_lines(lines: list[str]) -> tuple[int, list[tuple[Optional[str], float, bool, str, list[float]]]]:
    """Index of the first stat line, and (sign, value, has_percent, label, other readings) per stat line."""
    first, stats = -1, []
    for i, line in enumerate(lines):
        variants = number_variants(line)
        line = _clean(line)
        # A sign right before the number marks a stat; junk read in front of it ("3  +96.8%")
        # must not be taken for the value.
        m = re.search(r"[+\-−–xX×]\s?\d", line) or re.search(r"\d", line)
        if not m:
            if stats and line and not re.search(r"MR\s*\d", line) and len(line) < 12:
                stats[-1] = (*stats[-1][:3], stats[-1][3] + " " + line, stats[-1][4])  # wrapped label: "Bows)"
            continue
        parsed = STAT_LINE.match(line[m.start():])
        label = _label_of(parsed.group("label")) if parsed else ""
        if not parsed or len(label) < 3:
            continue
        sign = parsed.group("sign")
        value = float(parsed.group("num").replace(",", "."))
        stats.append((sign, value, bool(parsed.group("pct")), label, [v for v in variants if v != value]))
        if first < 0:
            first = i
    return first, stats


def parse_title(catalog: RivenCatalog, lines: list[str]) -> tuple[Optional[RivenWeapon], str, float]:
    """Weapon and riven name from the 1-2 lines above the stats ("Ceramic Dagger" / "Manti-parades")."""
    words_pool = [re.findall(r"[A-Za-z][A-Za-z\-]*", _clean(line)) for line in lines]
    candidates = []
    for take in (1, 2):
        if len(words_pool) >= take:
            words = [w for ws in words_pool[-take:] for w in ws]
            if len(words) >= 2:
                candidates.append(words)
    names = [w.name for w in catalog.weapons]
    best = (None, "", 0.0)
    for words in candidates:
        weapon_text, riven_name = " ".join(words[:-1]), words[-1]
        hit = process.extractOne(weapon_text, names, scorer=match_score)
        if hit and hit[1] > best[2]:
            best = (catalog.weapons[hit[2]], riven_name, hit[1])
    return best


def plausible(catalog: RivenCatalog, slug: str, value: float) -> bool:
    """Riven values stay within these bounds; OCR slips like "-722" (for -22%) or "968" (for 96.8%)
    land outside them."""
    stat = catalog.by_stat.get(slug)
    if stat is not None and stat.unit == "multiply":
        return 0.3 <= value <= 3.0
    return abs(value) <= 500


def _mode(values):
    values = [v for v in values if v is not None]
    return Counter(values).most_common(1)[0][0] if values else None


def read_riven(frame: np.ndarray, catalog: RivenCatalog, ui_scale: float = 1.0) -> RivenRead:
    """Read the card at several upscales and keep the majority answer for each field."""
    base = upscale_for(frame, ui_scale)
    with ThreadPoolExecutor(max_workers=len(VOTE_FACTORS)) as pool:  # each pass runs Tesseract
        reads = list(pool.map(lambda f: _read_once(frame, catalog, ui_scale, base * f), VOTE_FACTORS))
    found = [r for r in reads if r[0].ok]
    if not found:
        return reads[0][0]
    title = _mode([(r.draft.weapon, r.draft.name) for r, *_ in found])
    same_title = [r for r in found if (r[0].draft.weapon, r[0].draft.name) == title]
    shapes = [tuple((a.slug, a.positive) for a in r.draft.attrs) for r, *_ in same_title]
    # The name says how many buffs there are; a pass that dropped a line ("N +16.2% Damage") loses.
    slots = buffs_from_name(catalog, title[1], catalog.by_weapon.get(title[0])) if title[0] else None
    if slots is not None:
        complete = [sh for sh in shapes if sum(pos for _, pos in sh) == len(slots)]
        shapes = complete or shapes
    shape = _mode(shapes)
    agreeing = [r for r in same_title if tuple((a.slug, a.positive) for a in r[0].draft.attrs) == shape]

    mr = _mode([r[1] for r in reads])
    rolls = _mode([r[2] for r in reads])
    drain = _mode([r[3] for r in reads])
    rank = min(8, max(0, drain - BASE_DRAIN)) if drain else 0
    polarity = _mode([r[0].draft.polarity or None for r in reads])
    weapon = catalog.by_weapon.get(title[0])
    n_buffs = sum(pos for _, pos in shape)
    has_curse = any(not pos for _, pos in shape)
    unsure: list[str] = []

    def fits(slug: str, positive: bool, value: float) -> Optional[bool]:
        """Does this value lie in the stat's possible range (roll 0.85-1.15)? None if unknown."""
        if weapon is None:
            return None
        exp = expected_value(catalog, weapon, slug, positive, n_buffs, has_curse, rank)
        return None if not exp else 0.85 <= shown_value(catalog, slug, value) / exp <= 1.15

    def vote(i: int, slug: str, positive: bool) -> float:
        values = [r[0].draft.attrs[i].value for r in agreeing]
        # Each pass's own reading counts twice, its alternative readings ("/" as a 7) once.
        weighted = Counter()
        for r in agreeing:
            weighted[r[0].draft.attrs[i].value] += 2
            for alt in r[0].alternates.get(slug, ()):
                weighted[alt] += 1
        in_range = [v for v, _ in weighted.most_common() if fits(slug, positive, v)]
        if in_range:
            return in_range[0]
        good = [v for v in values if plausible(catalog, slug, v)]
        if not good:
            # Every pass lost the decimal point ("968" for 96.8): put it back before the last digit.
            good = [v / 10 for v in values if v == int(v) and plausible(catalog, slug, v / 10)]
        value = _mode(good or values)
        if fits(slug, positive, value) is False:
            unsure.append(catalog.stat_name(slug))
        return value

    attrs = tuple(RivenAttr(slug, vote(i, slug, positive), positive) for i, (slug, positive) in enumerate(shape))
    best = agreeing[0][0]
    warnings = [w for w in best.warnings if not (w.startswith("MR") and mr is not None)]
    if unsure:
        warnings.append(f"Check {', '.join(unsure)}: the value read is outside what this riven can roll.")
    if mr is None:
        warnings.append("MR not read: check it.")
    if polarity is None:
        warnings.append("Polarity not read: set it by hand.")
    draft = RivenDraft(weapon=title[0], name=title[1], attrs=attrs, mr=mr if mr is not None else 8,
                       rolls=rolls or 0, rank=rank, polarity=polarity or "madurai")
    return RivenRead(draft, warnings, best.raw)


def _read_once(frame: np.ndarray, catalog: RivenCatalog, ui_scale: float,
               up: float) -> tuple[RivenRead, Optional[int], Optional[int], Optional[int]]:
    """One OCR pass: (read, MR, rolls, drain)."""
    text_img = binarize(crop(frame, ref_box(frame, TEXT_BOX, ui_scale)), up)
    raw = ocr(text_img, psm=6)
    lines = [line for line in raw.splitlines() if line.strip()]
    first, stat_lines = parse_stat_lines(lines)
    warnings: list[str] = []
    if first < 0:
        return RivenRead(RivenDraft(), ["No riven card found. Open the riven in Item Details first."], raw), \
            None, None, None

    weapon, name, score = parse_title(catalog, lines[:first])
    if weapon is None or score < WEAPON_MIN:
        warnings.append("Weapon not recognised: search for it above.")
        weapon = None
    name = name.lower()

    # The name decides which stats are buffs; the labels are only matched among those.
    slots = buffs_from_name(catalog, name, weapon) if weapon else None
    if slots is None and weapon is not None and re.search(r"[l1|]", name):
        # "satl-visicon": a thin i read as l. Riven name parts never contain an l next to these spots,
        # so try each l/1/| as an i and keep the first spelling that parses.
        for i in [m.start() for m in re.finditer(r"[l1|]", name)]:
            fixed = name[:i] + "i" + name[i + 1:]
            slots = buffs_from_name(catalog, fixed, weapon)
            if slots is not None:
                name = fixed
                break
    if slots is None:
        warnings.append("The riven name did not read cleanly: check the buffs.")
    labels = _stat_labels(catalog, weapon)
    buff_slugs = [s for slot in (slots or []) for s in slot]
    attrs: list[RivenAttr] = []
    alternates: dict[str, list[float]] = {}
    for sign, value, _pct, label, others in stat_lines:
        hit = process.extractOne(label.lower(), list(labels), scorer=fuzz.ratio)
        if not hit or hit[1] < 60:
            warnings.append(f"Could not match “{label}” to a stat.")
            continue
        slug = labels[hit[0]]
        if slots is not None:
            # Prefer a buff from the name with a close label ("Damage" vs "Melee Damage").
            close = [s for s in buff_slugs if fuzz.ratio(label.lower(), catalog.stat_name(s).lower()) >= 60
                     or any(fuzz.ratio(label.lower(), a.lower()) >= 80 for a in LABEL_ALIASES.get(s, ()))]
            # Only when the label read poorly: a clean "Status Chance" curse must not become the
            # "Status Duration" buff just because the two look alike.
            if slug not in buff_slugs and close and hit[1] < 90:
                slug = close[0]
            positive = slug in buff_slugs and len([a for a in attrs if a.positive]) < len(slots)
        else:
            positive = sign not in ("-", "−", "–")
        stat = catalog.by_stat.get(slug)

        def signed(v: float) -> float:
            if not positive and stat and not stat.positive_is_negative and (stat.unit or "") != "multiply":
                return -abs(v)
            return -v if positive and sign in ("-", "−", "–") else v  # a recoil buff is a minus

        if any(a.slug == slug for a in attrs):
            continue
        attrs.append(RivenAttr(slug, signed(value), positive))
        alternates[slug] = [signed(v) for v in others]

    mr, rolls = read_bar(binarize(crop(frame, ref_box(frame, BAR_BOX, ui_scale)), up))
    drain = read_drain(binarize(crop(frame, ref_box(frame, DRAIN_BOX, ui_scale)), up))
    polarity = read_polarity(crop(frame, ref_box(frame, POLARITY_BOX, ui_scale)), up)
    draft = RivenDraft(weapon=weapon.slug if weapon else "", name=name, attrs=tuple(attrs),
                       polarity=polarity or "")
    return RivenRead(draft, warnings, raw, alternates), mr, rolls, drain
