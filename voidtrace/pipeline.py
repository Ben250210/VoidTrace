"""Glue: screenshot -> OCR (Phase 2) -> entity resolution (Phase 3) -> prices (Phase 4).

Two stages so the overlay can show cards before prices arrive: read() returns named cards in about
a second and a half; price_cards() fills prices concurrently and reports each update.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from typing import Callable, Optional

import numpy as np

from . import cv_cleaner
from .price_cleaner import PriceService, PriceStats
from .text_cleaner import EntityResolver, Resolution

log = logging.getLogger(__name__)

# Waiting for the reward cards to finish fading in (live test 4 read Burston Prime Barrel as Strun
# Prime Barrel from a frame grabbed mid-fade). The name band is grabbed repeatedly; once two grabs
# in a row barely differ, the screen has settled.
SETTLE_STEP_S = 0.12
SETTLE_TIMEOUT_S = 2.5
SETTLE_MIN_IOU = 0.85      # text masks of consecutive grabs must overlap this much
SETTLE_MAX_STRENGTH_CHANGE = 0.05  # and text brightness change less than 5%
SETTLE_MIN_TEXT = 0.01     # at least 1% of the band must be text before it can count as settled
SETTLE_STABLE_GRABS = 2
RETRY_DELAY_S = 0.6        # second look when a card is unreadable or uncertain
MAX_RETRIES = 2


class PipelineSkipped(Exception):
    """The pipeline decided not to show anything (wrong window, nothing recognised)."""


@dataclass(frozen=True)
class RewardCard:
    slot: int
    resolution: Resolution
    price: Optional[PriceStats]
    best: bool = False      # highest platinum value among the cards
    pending: bool = False   # price still being fetched

    @property
    def name(self) -> Optional[str]:
        return self.resolution.item.name if self.resolution.item else None

    @property
    def ducats(self) -> int:
        return self.resolution.item.ducats if self.resolution.item else 0

    @property
    def platinum(self) -> float:
        return self.price.price if self.price else 0.0


class RewardPipeline:
    def __init__(
        self,
        resolver: EntityResolver,
        prices: PriceService,
        capture: Callable[[], np.ndarray] = cv_cleaner.capture_screen,
        read_texts: Callable[..., list[str]] = cv_cleaner.read_reward_texts,
        players: Optional[int] = None,
        ui_scale: float = 1.0,
        foreground_check: Optional[Callable[[], bool]] = None,
    ) -> None:
        self.resolver = resolver
        self.prices = prices
        self.capture = capture
        self.read_texts = read_texts
        self.players = players
        self.ui_scale = ui_scale
        self.foreground_check = foreground_check

    def read(self, frame: Optional[np.ndarray] = None) -> list[RewardCard]:
        """Capture (unless given a frame), OCR and resolve. Prices are left pending."""
        t0 = time.perf_counter()
        live = frame is None
        if live:
            if self.foreground_check is not None and not self.foreground_check():
                raise PipelineSkipped("Warframe is not the active window")
            frame = self.capture_settled()
        t1 = time.perf_counter()
        texts = self.read_texts(frame, self.players, self.ui_scale)
        resolutions = self.resolver.resolve_all(texts)
        log.info("timing: capture %.2fs, OCR + resolve %.2fs", t1 - t0, time.perf_counter() - t1)
        retries = 0
        while live and retries < MAX_RETRIES and any(_weak(r) for r in resolutions):
            retries += 1
            time.sleep(RETRY_DELAY_S)
            again = self.resolver.resolve_all(self.read_texts(self.capture(), self.players, self.ui_scale))
            if len(again) == len(resolutions):
                resolutions = [_better(old, new) for old, new in zip(resolutions, again)]
            log.info("second look %d: %d weak card(s) left", retries, sum(_weak(r) for r in resolutions))
        if not any(res.item for res in resolutions):
            raise PipelineSkipped(f"no reward names recognised in {len(texts)} slots")
        cards = [
            RewardCard(slot, res, None, pending=res.item is not None and res.item.slug is not None)
            for slot, res in enumerate(resolutions)
        ]
        return mark_best(cards)

    def capture_settled(self) -> np.ndarray:
        """Capture once the reward names stop changing (cards finished fading in)."""
        t0 = time.perf_counter()
        prev, stable, frame, polarity = None, 0, None, None
        while True:
            frame = self.capture()
            if polarity is None:  # decided once: per-grab decisions can flip on a busy background
                polarity = cv_cleaner.text_polarity(_band(frame, self.ui_scale), upscale=1.0)
            sig = cv_cleaner.band_text_signature(_band(frame, self.ui_scale), polarity)
            if prev is not None and _settled(prev, sig):
                stable += 1
                if stable >= SETTLE_STABLE_GRABS:
                    break
            else:
                stable = 0
            if time.perf_counter() - t0 > SETTLE_TIMEOUT_S:
                log.info("names did not settle within %.1fs; reading anyway", SETTLE_TIMEOUT_S)
                break
            prev = sig
            time.sleep(SETTLE_STEP_S)
        log.info("timing: names settled after %.2fs", time.perf_counter() - t0)
        return frame

    def price_cards(
        self,
        cards: list[RewardCard],
        on_update: Optional[Callable[[list[RewardCard]], None]] = None,
    ) -> list[RewardCard]:
        """Fetch every pending price concurrently; call on_update after each one lands."""
        cards = list(cards)
        todo = [c for c in cards if c.pending]
        if not todo:
            return cards
        t0 = time.perf_counter()
        with ThreadPoolExecutor(max_workers=len(todo)) as pool:
            futures = {pool.submit(self.prices.get_price, c.resolution.item.slug): c.slot for c in todo}
            for future in as_completed(futures):
                slot = futures[future]
                try:
                    price = future.result()
                except Exception:
                    log.exception("price lookup failed for slot %d", slot)
                    price = None
                cards[slot] = replace(cards[slot], price=price, pending=False)
                cards = mark_best(cards)
                log.info("timing: price for %s after %.2fs", cards[slot].name, time.perf_counter() - t0)
                if on_update is not None:
                    on_update(list(cards))
        return cards

    def run(self, frame: Optional[np.ndarray] = None) -> list[RewardCard]:
        return self.price_cards(self.read(frame))


def _band(frame: np.ndarray, ui_scale: float) -> np.ndarray:
    h, w = frame.shape[:2]
    return cv_cleaner.crop(frame, cv_cleaner.reward_band(w, h, ui_scale))


def _settled(prev: tuple[np.ndarray, float], cur: tuple[np.ndarray, float]) -> bool:
    (m0, s0), (m1, s1) = prev, cur
    if m0.shape != m1.shape or m1.mean() < SETTLE_MIN_TEXT or s0 <= 0:
        return False
    iou = np.logical_and(m0, m1).sum() / max(1, np.logical_or(m0, m1).sum())
    return iou >= SETTLE_MIN_IOU and abs(s1 - s0) / s0 < SETTLE_MAX_STRENGTH_CHANGE


def _weak(res: Resolution) -> bool:
    return res.item is None or res.ambiguous


def _better(old: Resolution, new: Resolution) -> Resolution:
    """Keep the stronger of two reads of the same slot: confident beats uncertain beats none."""
    def rank(r: Resolution) -> tuple:
        return (r.item is not None, not r.ambiguous, r.score)
    return new if rank(new) > rank(old) else old


def mark_best(cards: list[RewardCard]) -> list[RewardCard]:
    """Flag the most valuable card by platinum, breaking ties on ducats."""
    candidates = [c for c in cards if c.resolution.item is not None]
    if not candidates:
        return cards
    best = max(candidates, key=lambda c: (c.platinum, c.ducats))
    return [replace(c, best=c is best) for c in cards]
