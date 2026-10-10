"""Phase 2: locate reward-name text on the relic reward screen and clean it up for OCR.

Geometry follows WFInfo's reference layout (github.com/WFCD/WFinfo, Ocr.cs), defined at
1920x1080 with 100% in-game UI scale and scaled to the actual resolution. Ultrawide screens
keep the UI in a centered 16:9 area, so scaling is driven by height when the aspect ratio is
16:9 or wider.
"""

from __future__ import annotations

import os
import shutil
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


def flatten_illumination(gray: np.ndarray) -> np.ndarray:
    """Remove smooth large-scale light (gradients, bloom, translucent panels), keep thin strokes.

    A morphological top-hat keeps bright details smaller than the kernel; a black-hat keeps
    dark ones. Whichever responds more strongly tells us the text polarity, so this works for
    light-on-dark and dark-on-light UI themes. Output: text bright on a near-black background.
    """
    k = max(9, int(gray.shape[0] * 0.2)) | 1  # odd, larger than a letter stroke
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    tophat = cv2.morphologyEx(gray, cv2.MORPH_TOPHAT, kernel)
    blackhat = cv2.morphologyEx(gray, cv2.MORPH_BLACKHAT, kernel)
    return tophat if tophat.sum() >= blackhat.sum() else blackhat


def preprocess(img: np.ndarray, upscale: float = 2.0, blur_ksize: int = 3, pad: int = 10) -> np.ndarray:
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
    flat = flatten_illumination(gray)
    _, binary = cv2.threshold(flat, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    binary = cv2.bitwise_not(binary)  # text was bright; make it black on white
    if pad:
        binary = cv2.copyMakeBorder(binary, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=255)
    return binary


def text_mask(img: np.ndarray) -> np.ndarray:
    """Boolean mask of text pixels (True = text) using the same cleaning as preprocess()."""
    return preprocess(img, upscale=1.0, pad=0) == 0


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
        inside = sum(mass[k] for k in centers) / len(centers)
        others = [k for k in mass if k not in centers]
        outside = sum(mass[k] for k in others) / len(others)
        score = inside - outside
        if score > best_score:
            best, best_score = players, score
    return best


# --- OCR --------------------------------------------------------------------

DEFAULT_TESSERACT = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
CHAR_WHITELIST = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789&'- "


class TesseractNotFound(RuntimeError):
    pass


def find_tesseract() -> Optional[str]:
    """Locate tesseract.exe: TESSERACT_CMD env var, then PATH, then the default install dir."""
    env = os.environ.get("TESSERACT_CMD")
    if env and Path(env).exists():
        return env
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
    config = f'--psm {psm} -c tessedit_char_whitelist="{CHAR_WHITELIST}"'
    return pytesseract.image_to_string(binary, config=config)


# --- capture + pipeline ------------------------------------------------------

def capture_screen(monitor: int = 1) -> np.ndarray:
    """Grab one monitor as a BGR image. Read-only screen capture; touches nothing in the game."""
    import mss

    with mss.mss() as sct:
        shot = np.asarray(sct.grab(sct.monitors[monitor]))
    return cv2.cvtColor(shot, cv2.COLOR_BGRA2BGR)


def read_reward_texts(
    frame: np.ndarray,
    players: Optional[int] = None,
    ui_scale: float = 1.0,
    ocr_fn: Callable[[np.ndarray], str] = ocr,
    debug_dir: Optional[Path] = None,
) -> list[str]:
    """Full Phase 2 pipeline: frame -> per-card cleaned crops -> raw OCR strings.

    `players` is detected from the frame when not given.
    """
    h, w = frame.shape[:2]
    if players is None:
        players = detect_player_count(crop(frame, reward_band(w, h, ui_scale)))
    texts = []
    for i, box in enumerate(reward_slots(w, h, players, ui_scale)):
        raw = crop(frame, box)
        clean = preprocess(raw)
        if debug_dir is not None:
            debug_dir.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(debug_dir / f"slot{i}_raw.png"), raw)
            cv2.imwrite(str(debug_dir / f"slot{i}_clean.png"), clean)
        texts.append(ocr_fn(clean))
    return texts
