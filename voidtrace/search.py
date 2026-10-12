"""Shop-style name search used by the Market and Rivens search boxes ("arcane ener" -> Arcane Energize,
"nekrso" -> Nekros). Works on anything with a `.name`."""

from __future__ import annotations

from typing import Sequence, TypeVar

from rapidfuzz import fuzz, utils

T = TypeVar("T")

MAX_SUGGESTIONS = 8


# Short forms players type in trade chat.
TYPO_MIN = 70  # per-word similarity needed to count as a typo of that word


ALIASES = {"bp": "blueprint", "sys": "systems", "neuro": "neuroptics", "chas": "chassis", "rec": "receiver"}


def _words(text: str) -> list[str]:
    return utils.default_process(text).split()


def search_catalog(query: str, catalog: Sequence[T], limit: int = MAX_SUGGESTIONS) -> list[T]:
    """Shop-style search, best tier first:
        0  name starts with the query               "prim"        -> Primed ...
        1  every query word starts a name word, in order   "arcane ener" -> Arcane Energize
        2  every query word starts a name word, any order
        3  query appears inside the name
        4  only when nothing above matched: each word within a typo of a name word ("nekrso" -> Nekros)
    Ties go to the shorter name ("Serration" before "Spectral Serration"), typos to the closer match.
    WRatio alone ranked hundreds of names equally and fell back to alphabetical order.
    """
    q_words = [ALIASES.get(w, w) for w in _words(query)]
    if not q_words or len(" ".join(q_words)) < 2:
        return []
    q = " ".join(q_words)
    scored = []
    for item in catalog:
        name = " ".join(_words(item.name))
        n_words = name.split()
        if name.startswith(q):
            tier = 0
        elif _prefix_in_order(q_words, n_words):
            tier = 1
        elif all(any(w.startswith(qw) for w in n_words) for qw in q_words):
            tier = 2
        elif q in name:
            tier = 3
        else:
            continue
        scored.append((tier, 0.0, len(name), name, item))
    if not scored:
        # Typos only: match each typed word to its closest name word (or the start of one).
        for item in catalog:
            n_words = _words(item.name)
            best = [max(max(fuzz.ratio(qw, w), fuzz.ratio(qw, w[:len(qw) + 1])) for w in n_words) for qw in q_words]
            if min(best) >= TYPO_MIN:
                name = " ".join(n_words)
                scored.append((4, -round(sum(best) / len(best)), len(name), name, item))
    scored.sort(key=lambda s: s[:4])
    return [s[4] for s in scored[:limit]]


def _prefix_in_order(q_words: list[str], n_words: list[str]) -> bool:
    i = 0
    for w in n_words:
        if i < len(q_words) and w.startswith(q_words[i]):
            i += 1
    return i == len(q_words)
