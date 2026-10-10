"""Phase 3: normalize raw OCR strings and resolve them to canonical item names."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

from rapidfuzz import fuzz, process, utils

from .items import Item

ALLOWED_CHARS_RE = re.compile(r"[^A-Za-z0-9&' -]+")
WHITESPACE_RE = re.compile(r"\s+")

# Typical OCR confusions of letters for digits/symbols. Applied only inside tokens that also
# contain letters, since no relic reward name has a standalone number.
LEET_MAP = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "8": "b"})

DEFAULT_CUTOFF = 75.0
AMBIGUITY_MARGIN = 3.0  # top-two score gap below which a match is flagged ambiguous


def normalize(raw: str) -> str:
    """Clean one OCR string: drop illegal chars, fix leetspeak, collapse repeats and noise."""
    text = WHITESPACE_RE.sub(" ", raw.replace("|", "I"))
    text = ALLOWED_CHARS_RE.sub(" ", text)
    tokens = []
    for token in text.split():
        if any(ch.isalpha() for ch in token):
            token = token.translate(LEET_MAP)
        elif token.isdigit():
            continue  # stray numbers are OCR noise here
        if len(token) == 1 and token not in {"&"}:
            continue  # single stray glyphs from card borders
        if tokens and token.casefold() == tokens[-1].casefold():
            continue  # "PRIME PRIME" from a duplicated read
        tokens.append(token)
    return " ".join(tokens).title()


@dataclass(frozen=True)
class Resolution:
    raw: str
    cleaned: str
    item: Optional[Item]
    score: float
    ambiguous: bool = False


class EntityResolver:
    """Fuzzy-matches cleaned OCR text against the master item list."""

    def __init__(self, items: Iterable[Item], cutoff: float = DEFAULT_CUTOFF) -> None:
        self.items = list(items)
        self.cutoff = cutoff
        self._names = [item.name for item in self.items]
        self._memo: dict[str, Resolution] = {}

    def resolve(self, raw: str) -> Resolution:
        cleaned = normalize(raw)
        if cleaned in self._memo:
            cached = self._memo[cleaned]
            return Resolution(raw, cleaned, cached.item, cached.score, cached.ambiguous)
        result = self._match(raw, cleaned)
        self._memo[cleaned] = result
        return result

    def resolve_all(self, raws: Sequence[str]) -> list[Resolution]:
        return [self.resolve(raw) for raw in raws]

    def _match(self, raw: str, cleaned: str) -> Resolution:
        if not cleaned:
            return Resolution(raw, cleaned, None, 0.0)
        top = process.extract(
            cleaned, self._names, scorer=fuzz.WRatio, processor=utils.default_process, limit=2
        )
        if not top or top[0][1] < self.cutoff:
            return Resolution(raw, cleaned, None, top[0][1] if top else 0.0)
        _, best_score, best_index = top[0]
        ambiguous = len(top) > 1 and best_score - top[1][1] < AMBIGUITY_MARGIN
        return Resolution(raw, cleaned, self.items[best_index], best_score, ambiguous)
