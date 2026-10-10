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
        if frame is None:
            if self.foreground_check is not None and not self.foreground_check():
                raise PipelineSkipped("Warframe is not the active window")
            frame = self.capture()
        t1 = time.perf_counter()
        texts = self.read_texts(frame, self.players, self.ui_scale)
        t2 = time.perf_counter()
        resolutions = self.resolver.resolve_all(texts)
        log.info("timing: capture %.2fs, OCR %.2fs, resolve %.2fs", t1 - t0, t2 - t1, time.perf_counter() - t2)
        if not any(res.item for res in resolutions):
            raise PipelineSkipped(f"no reward names recognised in {len(texts)} slots")
        cards = [
            RewardCard(slot, res, None, pending=res.item is not None and res.item.slug is not None)
            for slot, res in enumerate(resolutions)
        ]
        return mark_best(cards)

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


def mark_best(cards: list[RewardCard]) -> list[RewardCard]:
    """Flag the most valuable card by platinum, breaking ties on ducats."""
    candidates = [c for c in cards if c.resolution.item is not None]
    if not candidates:
        return cards
    best = max(candidates, key=lambda c: (c.platinum, c.ducats))
    return [replace(c, best=c is best) for c in cards]
