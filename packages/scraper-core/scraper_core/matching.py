"""Free-text-vs-search-term matching helpers.

Built from two real production findings (PA SPEAKERS, PLAGG): keyword/substring
matching between a scraped listing's title and a normalized search term fails
silently whenever the listing's text doesn't share a literal substring with the
term - a real match is simply never found, with no error. Two distinct causes
were observed, so this module has two distinct fixes:

1. Word-level synonyms across languages/spelling (PLAGG): "kuling jakke" never
   matched "Kuling jacka 110" (Swedish spelling) or "kurtka" (Polish) because
   they share no substring at all. Fix: `build_synonym_lookup()` groups such
   words into clusters up front.
2. Glued model-number suffixes (PA SPEAKERS): "RCF ART 710" never matched
   "RCF ART-710A-MK5", and "Yamaha DXR8" never matched "Yamaha DXR8MKII",
   because there is no word boundary at all between the base model number and
   its generation/version suffix. Fix: `normalize_model_number()` strips a
   known set of suffix patterns before comparison.

Both are heuristic and will need extending as new real-world patterns turn up
- see SCRAPING_LESSONS.md. They are deliberately separate functions: don't
conflate "different word for the same thing" with "same model, different
suffix formatting".
"""

from __future__ import annotations

import re
from collections.abc import Iterable

_GENERATION_SUFFIX_RE = re.compile(
    r"""
    [-\s]*                     # optional separator right before the suffix
    (?:
        MK\s*[IVXLC0-9]+        # MK5, MKII, MK 5, Mk III
        |MARK\s*[IVXLC0-9]+     # Mark II
        |[IVX]{2,4}\b           # standalone roman numerals (2+ chars - skip lone "I")
        |(?<=[0-9])[A-Z]\b      # a single trailing letter glued directly to digits, e.g. "710A"
    )
    \s*$                        # only strip a suffix at the very end of the string
    """,
    re.IGNORECASE | re.VERBOSE,
)


def normalize_model_number(text: str) -> str:
    """Strips generation/version suffixes (MK5, MKII, trailing letter after
    digits, ...) from the end of a model-number-like string, so a listing
    title's "RCF ART-710A-MK5" and a search term's "RCF ART 710" normalize to
    the same value ("RCF ART 710") and can be compared with a plain equality
    or substring check.

    Iterates because suffixes can chain (e.g. "710A-MK5" has two to strip, in
    order). Only strips from the END of the string - never touches the base
    model number itself.
    """
    normalized = text.strip().upper()
    while True:
        stripped = _GENERATION_SUFFIX_RE.sub("", normalized).strip(" -")
        if stripped == normalized:
            break
        normalized = stripped
    return re.sub(r"[-\s]+", " ", normalized).strip()


def build_synonym_lookup(clusters: Iterable[Iterable[str]]) -> dict[str, frozenset[str]]:
    """Expands word clusters into a flat lookup: every word in a cluster maps
    to the cluster's full (lowercased) word set, including itself.

    Example:
        clusters = [{"jakke", "jacka", "kurtka"}, {"bukser", "spodnie"}]
        lookup = build_synonym_lookup(clusters)
        lookup["jacka"] == frozenset({"jakke", "jacka", "kurtka"})

    Use with `expand_synonyms()` when tokenizing a search term, so "jakke"
    also matches listing text containing "jacka" or "kurtka".
    """
    lookup: dict[str, frozenset[str]] = {}
    for cluster in clusters:
        frozen = frozenset(word.lower() for word in cluster)
        for word in frozen:
            lookup[word] = frozen
    return lookup


def expand_synonyms(word: str, lookup: dict[str, frozenset[str]]) -> frozenset[str]:
    """Returns `word`'s full synonym cluster (including itself), or just
    `{word}` if it isn't part of any tracked cluster."""
    return lookup.get(word.lower(), frozenset({word.lower()}))
