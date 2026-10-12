"""Phase 2: locate reward-name text on the relic reward screen and clean it up for OCR.

Geometry follows WFInfo's reference layout (github.com/WFCD/WFinfo, Ocr.cs), defined at
1920x1080 with 100% in-game UI scale and scaled to the actual resolution. Ultrawide screens
keep the UI in a centered 16:9 area, so scaling is driven by height when the aspect ratio is
16:9 or wider.
"""

from __future__ import annotations

import os
import shutil
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import cv2
import numpy as np

# Reference layout at 1920x1080, UI scale 100% (WFInfo constants).
REF_WIDTH = 1920
REF_HEIGHT = 1080
REWARD_WIDTH = 968        # width of the full 4-card reward row
REWARD_HEIGHT = 235
REWARD_Y_DISPLAY = 316
REWARD_LINE_HEIGHT = 48   # one line of reward-name text

MAX_PLAYERS = 4


@dataclass(frozen=True)
class Box:
    x: int
    y: int
    w: int
    h: int


def screen_scaling(width: int, height: int) -> float:
    """Scale factor from the 1920x1080 reference to this screen."""
    if width / height >= REF_WIDTH / REF_HEIGHT:
        return height / REF_HEIGHT
    return width / REF_WIDTH


def reward_band(width: int, height: int, ui_scale: float = 1.0) -> Box:
    """The horizontal strip holding all reward names (up to two text lines)."""
    s = screen_scaling(width, height) * ui_scale
    band_w = int(REWARD_WIDTH * s)
    left = width // 2 - band_w // 2
    top = height // 2 - int((REWARD_Y_DISPLAY - REWARD_HEIGHT + REWARD_LINE_HEIGHT) * s)
    bottom = height // 2 - int((REWARD_Y_DISPLAY - REWARD_HEIGHT) * s * 0.5)
    return Box(max(0, left), max(0, top), min(band_w, width), max(1, bottom - top))


def slot_centers(band: Box, players: int) -> list[int]:
    """X centers of each reward card. Cards are band.w/4 wide and centered as a group."""
    if not 1 <= players <= MAX_PLAYERS:
        raise ValueError(f"players must be 1..{MAX_PLAYERS}, got {players}")
    card_w = band.w / MAX_PLAYERS
    mid = band.x + band.w / 2
    return [int(mid + (i - (players - 1) / 2) * card_w) for i in range(players)]


def reward_slots(width: int, height: int, players: int, ui_scale: float = 1.0) -> list[Box]:
    """One crop box per reward card, left to right."""
    band = reward_band(width, height, ui_scale)
    card_w = band.w // MAX_PLAYERS
    return [Box(c - card_w // 2, band.y, card_w, band.h) for c in slot_centers(band, players)]


def crop(frame: np.ndarray, box: Box) -> np.ndarray:
    """Crop with bounds clamped to the frame."""
    h, w = frame.shape[:2]
    x0, y0 = max(0, box.x), max(0, box.y)
    x1, y1 = min(w, box.x + box.w), min(h, box.y + box.h)
    return frame[y0:y1, x0:x1]


def _hats(gray: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    k = max(9, int(gray.shape[0] * 0.2)) | 1  # odd, larger than a letter stroke
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    return cv2.morphologyEx(gray, cv2.MORPH_TOPHAT, kernel), cv2.morphologyEx(gray, cv2.MORPH_BLACKHAT, kernel)


def flatten_illumination(gray: np.ndarray, polarity: Optional[str] = None) -> np.ndarray:
    """Remove smooth large-scale light (gradients, bloom, translucent panels), keep thin strokes.

    A morphological top-hat keeps bright details smaller than the kernel; a black-hat keeps
    dark ones. `polarity` "light" (light text) picks the top-hat, "dark" the black-hat. Without it,
    the stronger peak response decides. Peaks, not sums: on a noisy panel the many weak noise
    responses can outweigh the few strong text strokes in a sum.
    Output: text bright on a near-black background.
    """
    tophat, blackhat = _hats(gray)
    if polarity is None:
        polarity = "light" if np.percentile(tophat, 99.5) >= np.percentile(blackhat, 99.5) else "dark"
    return tophat if polarity == "light" else blackhat


def text_polarity(img: np.ndarray, upscale: float = 2.0) -> str:
    """"light" or "dark" text, decided over a whole region (the full name band).

    Per-slot decisions failed on live test 3: dark item art reaching into two slots made their
    black-hat response win, and those slots came out inverted (white text in black blobs). All
    names on one screen share a colour, so the band decides once for every slot.
    """
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    if upscale != 1.0:
        gray = cv2.resize(gray, None, fx=upscale, fy=upscale, interpolation=cv2.INTER_CUBIC)
    tophat, blackhat = _hats(gray)
    return "light" if np.percentile(tophat, 99.5) >= np.percentile(blackhat, 99.5) else "dark"


def remove_specks(text_white: np.ndarray, min_rel_height: float = 0.45) -> np.ndarray:
    """Drop connected blobs much shorter than the letters (particles, sparks, dust).

    Letter height is estimated as the 90th percentile of blob heights, since the letters are
    the tallest common blobs once smooth light has been flattened away.
    """
    n, labels, stats, _ = cv2.connectedComponentsWithStats(text_white, connectivity=8)
    if n <= 1:
        return text_white
    heights = stats[1:, cv2.CC_STAT_HEIGHT]
    ref = np.percentile(heights, 90)
    keep = np.zeros(n, dtype=bool)
    keep[1:] = heights >= min_rel_height * ref
    return np.where(keep[labels], 255, 0).astype(np.uint8)


def preprocess(
    img: np.ndarray, upscale: float = 2.0, blur_ksize: int = 3, pad: int = 10, polarity: Optional[str] = None
) -> np.ndarray:
    """Grayscale -> upscale -> blur -> flatten illumination -> Otsu -> black text on white.

    Otsu on the raw crop would split background from bloom rather than text from everything
    else, so the background light is flattened first and Otsu then separates text cleanly.
    Output is black text on white (what Tesseract reads best) regardless of UI theme.
    """
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    if upscale != 1.0:
        gray = cv2.resize(gray, None, fx=upscale, fy=upscale, interpolation=cv2.INTER_CUBIC)
    if blur_ksize > 1:
        gray = cv2.GaussianBlur(gray, (blur_ksize, blur_ksize), 0)
    flat = flatten_illumination(gray, polarity)
    _, binary = cv2.threshold(flat, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    binary = remove_specks(binary)
    binary = cv2.bitwise_not(binary)  # text was bright; make it black on white
    if pad:
        binary = cv2.copyMakeBorder(binary, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=255)
    return binary


def text_mask(img: np.ndarray) -> np.ndarray:
    """Boolean mask of text pixels (True = text) using the same cleaning as preprocess()."""
    return preprocess(img, upscale=1.0, pad=0) == 0


def band_text_signature(band_img: np.ndarray, polarity: Optional[str] = None) -> tuple[np.ndarray, float]:
    """(text mask, mean text strength) for the reward-name band, used to tell when names stop fading in.

    Only text pixels count: the live 3D background behind the reward screen keeps moving (measured
    20-55 grey levels of change between grabs), so whole-image differences never settle.
    """
    gray = cv2.cvtColor(band_img, cv2.COLOR_BGR2GRAY) if band_img.ndim == 3 else band_img
    flat = flatten_illumination(cv2.GaussianBlur(gray, (3, 3), 0), polarity)
    _, binary = cv2.threshold(flat, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    mask = remove_specks(binary) > 0
    strength = float(flat[mask].mean()) if mask.any() else 0.0
    return mask, strength


def detect_player_count(band_img: np.ndarray) -> int:
    """Guess squad size from where text sits in the reward band.

    Card centers fall on eighths of the band: 4 players use 1,3,5,7; 3 use 2,4,6; 2 use 3,5;
    1 uses 4. Score each layout by text mass near its centers minus mass near the others.
    """
    mask = text_mask(band_img)
    columns = mask.sum(axis=0).astype(float)
    w = columns.size
    if columns.sum() == 0:
        return MAX_PLAYERS
    half_win = max(1, w // 32)

    def mass_at(eighth: int) -> float:
        c = int(w * eighth / 8)
        return columns[max(0, c - half_win): c + half_win].sum()

    mass = {k: mass_at(k) for k in range(1, 8)}
    layouts = {4: (1, 3, 5, 7), 3: (2, 4, 6), 2: (3, 5), 1: (4,)}
    best, best_score = MAX_PLAYERS, float("-inf")
    for players, centers in layouts.items():
        # Every card of a layout must carry text, and its gaps must not: weakest center minus
        # strongest gap. Averages let two strong middle names outvote empty outer slots
        # (live test 3 read a 4-card screen as 2 cards).
        others = [k for k in mass if k not in centers]
        score = min(mass[k] for k in centers) - max(mass[k] for k in others)
        if score > best_score:
            best, best_score = players, score
    return best


# --- OCR --------------------------------------------------------------------

DEFAULT_TESSERACT = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
# No character whitelist: in Tesseract 5 a whitelist without a space glues words together
# ("NEKROSPRIMEBLUEPRINT"), and a quoted one breaks pytesseract's config parsing.
# Illegal characters are stripped in Phase 3 instead.


class TesseractNotFound(RuntimeError):
    pass


def bundled_tesseract() -> Optional[Path]:
    """tesseract.exe shipped next to VoidTrace.exe in the release zip (tesseract/ folder)."""
    if getattr(sys, "frozen", False):
        candidate = Path(sys.executable).resolve().parent / "tesseract" / "tesseract.exe"
        if candidate.exists():
            return candidate
    return None


def find_tesseract() -> Optional[str]:
    """Locate tesseract.exe: TESSERACT_CMD env var, the copy bundled with the release, PATH,
    then the default install dir."""
    env = os.environ.get("TESSERACT_CMD")
    if env and Path(env).exists():
        return env
    bundled = bundled_tesseract()
    if bundled is not None:
        return str(bundled)
    on_path = shutil.which("tesseract")
    if on_path:
        return on_path
    if DEFAULT_TESSERACT.exists():
        return str(DEFAULT_TESSERACT)
    return None


def ocr(binary: np.ndarray, psm: int = 6) -> str:
    """Run Tesseract on a preprocessed crop. psm 6 = a uniform block of text (names wrap to 2 lines)."""
    import pytesseract

    cmd = find_tesseract()
    if cmd is None:
        raise TesseractNotFound(
            "Tesseract OCR not found. Install it (e.g. `winget install UB-Mannheim.TesseractOCR`) "
            "or set TESSERACT_CMD to tesseract.exe."
        )
    pytesseract.pytesseract.tesseract_cmd = cmd
    return pytesseract.image_to_string(binary, config=f"--psm {psm}")


# --- capture + pipeline ------------------------------------------------------

def centre_region(width: int, height: int, fw: float, fh: float) -> tuple[int, int, int, int]:
    """(x, y, w, h) of the centred part of a screen that is `fw` of its width and `fh` of its height."""
    w, h = int(width * fw), int(height * fh)
    return (width - w) // 2, (height - h) // 2, w, h


_grabbers = threading.local()  # one screen grabber per thread (they are not shared across threads)


def capture_screen(monitor: int = 1, centre: Optional[tuple[float, float]] = None) -> np.ndarray:
    """Grab one monitor as a BGR image. Read-only screen capture; touches nothing in the game.

    `centre=(fw, fh)` grabs only that centred part (the rest of the image stays black): a full
    2560x1600 grab takes ~55 ms, the centre eighth a fraction of that."""
    import mss

    sct = getattr(_grabbers, "sct", None)
    if sct is None:
        sct = _grabbers.sct = (getattr(mss, "MSS", None) or mss.mss)()
    mon = sct.monitors[monitor]
    if centre is None:
        return cv2.cvtColor(np.asarray(sct.grab(mon)), cv2.COLOR_BGRA2BGR)
    x, y, w, h = centre_region(mon["width"], mon["height"], *centre)
    part = np.asarray(sct.grab({"left": mon["left"] + x, "top": mon["top"] + y, "width": w, "height": h}))
    frame = np.zeros((mon["height"], mon["width"], 3), dtype=np.uint8)
    frame[y:y + h, x:x + w] = part[:, :, :3]
    return frame


def read_reward_texts(
    frame: np.ndarray,
    players: Optional[int] = None,
    ui_scale: float = 1.0,
    ocr_fn: Callable[[np.ndarray], str] = ocr,
    debug_dir: Optional[Path] = None,
) -> list[str]:
    """Full Phase 2 pipeline: frame -> per-card cleaned crops -> raw OCR strings.

    `players` is detected from the frame when not given. Slots are read in parallel: each Tesseract
    call is its own process, so four slots take about as long as one (2.3 s -> ~0.7 s measured).
    """
    h, w = frame.shape[:2]
    if players is None:
        players = detect_player_count(crop(frame, reward_band(w, h, ui_scale)))
    raws = [crop(frame, box) for box in reward_slots(w, h, players, ui_scale)]
    polarity = text_polarity(crop(frame, reward_band(w, h, ui_scale)))
    cleaned = [preprocess(raw, polarity=polarity) for raw in raws]
    if debug_dir is not None:
        debug_dir.mkdir(parents=True, exist_ok=True)
        for i, (raw, clean) in enumerate(zip(raws, cleaned)):
            cv2.imwrite(str(debug_dir / f"slot{i}_raw.png"), raw)
            cv2.imwrite(str(debug_dir / f"slot{i}_clean.png"), clean)
    with ThreadPoolExecutor(max_workers=max(1, len(cleaned))) as pool:
        texts = list(pool.map(ocr_fn, cleaned))
        # Safety net: a slot that reads almost nothing is retried with the opposite polarity.
        retry = [i for i, text in enumerate(texts) if _letters(text) < MIN_LETTERS]
        if retry:
            other = "dark" if polarity == "light" else "light"
            redo = list(pool.map(ocr_fn, [preprocess(raws[i], polarity=other) for i in retry]))
            for i, text in zip(retry, redo):
                if _letters(text) > _letters(texts[i]):
                    texts[i] = text
    return texts


MIN_LETTERS = 6  # shortest relic reward name has more letters than this


def _letters(text: str) -> int:
    return sum(ch.isalpha() for ch in text)


def warframe_is_foreground(pid_finder: Optional[Callable[[], Optional[int]]] = None) -> bool:
    """True when the active window belongs to Warframe. Capturing anything else (a terminal, a
    browser, the desktop) would OCR the wrong screen, which the second live test did."""
    if sys.platform != "win32":
        return True
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32")
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return False
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if pid_finder is not None:
        return pid.value == pid_finder()
    # Ask only about the window in front: listing every process took ~30 ms, every 0.7 s.
    from .debug_log import process_name

    name = process_name(pid.value).lower()
    return name.startswith("warframe") and name.endswith(".exe")
