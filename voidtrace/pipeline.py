"""Glue: screenshot -> OCR (Phase 2) -> entity resolution (Phase 3) -> prices (Phase 4)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np

from . import cv_cleaner
from .price_cleaner import PriceService, PriceStats
from .text_cleaner import EntityResolver, Resolution


@dataclass(frozen=True)
class RewardCard:
    slot: int
    resolution: Resolution
    price: Optional[PriceStats]
    best: bool = False  # highest platinum value among the cards

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
    ) -> None:
        self.resolver = resolver
        self.prices = prices
        self.capture = capture
        self.read_texts = read_texts
        self.players = players
        self.ui_scale = ui_scale

    def run(self, frame: Optional[np.ndarray] = None) -> list[RewardCard]:
        if frame is None:
            frame = self.capture()
        texts = self.read_texts(frame, self.players, self.ui_scale)
        cards = []
        for slot, res in enumerate(self.resolver.resolve_all(texts)):
            price = None
            if res.item is not None and res.item.slug is not None:
                price = self.prices.get_price(res.item.slug)
            cards.append(RewardCard(slot, res, price))
        return mark_best(cards)


def mark_best(cards: list[RewardCard]) -> list[RewardCard]:
    """Flag the most valuable card by platinum, breaking ties on ducats."""
    candidates = [c for c in cards if c.resolution.item is not None]
    if not candidates:
        return cards
    best = max(candidates, key=lambda c: (c.platinum, c.ducats))
    return [RewardCard(c.slot, c.resolution, c.price, best=c is best) for c in cards]
