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

# Calibrated on 22 labelled OCR reads (2 live screenshots, a harsh synthetic screen, unit cases):
# correct matches scored 67-100, the best junk match (end screen, terminal text) 43.
DEFAULT_CUTOFF = 60.0
AMBIGUITY_MARGIN = 3.0  # top-two score gap below which a match is flagged ambiguous


def match_score(a: str, b: str, **_kwargs) -> float:
    """Average of token_set_ratio and ratio.

    WRatio, used first, falls back to partial matching on messy text and scores every name sharing
    "Prime Blade" the same 85.5; on the second live screenshot it turned "Restrel Prime Bia" into
    Akstiletto Prime Blueprint. token_set_ratio ignores junk words around the name; ratio keeps
    "Saryn Prime Blueprint" from beating "Saryn Prime Systems Blueprint" on a subset match.
    """
    return 0.5 * fuzz.token_set_ratio(a, b) + 0.5 * fuzz.ratio(a, b)


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
            cleaned, self._names, scorer=match_score, processor=utils.default_process, limit=2
        )
        if not top or top[0][1] < self.cutoff:
            return Resolution(raw, cleaned, None, top[0][1] if top else 0.0)
        best_name, best_score, best_index = top[0]
        ambiguous = len(top) > 1 and best_score - top[1][1] < AMBIGUITY_MARGIN
        # A truncated read ("Nekros Prime") is wholly contained in several names: say so.
        query = utils.default_process(cleaned)
        if len(top) > 1 and query != utils.default_process(best_name):
            contained = [n for n, _, _ in top if fuzz.token_set_ratio(query, utils.default_process(n)) == 100]
            ambiguous = ambiguous or len(contained) > 1
        return Resolution(raw, cleaned, self.items[best_index], best_score, ambiguous)
