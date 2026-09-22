"""Stage 1 - hard pass/fail filters (spec §3)."""

from __future__ import annotations

from dataclasses import dataclass

from . import arabic
from .config import ScoringConfig


@dataclass
class Verse:
    surah: int
    ayah: int
    text: str
    word_count: int


@dataclass
class DroppedVerse:
    surah: int
    ayah: int
    text: str
    reason: str


def apply_hard_filters(
    verses: list[Verse], config: ScoringConfig
) -> tuple[list[Verse], list[DroppedVerse]]:
    """Return ``(kept, dropped)`` after the three Stage 1 filters.

    Filters run in spec order: word count, full diacritization, dedup.
    """
    dropped: list[DroppedVerse] = []
    kept: list[Verse] = []

    # 1) word-count range
    for verse in verses:
        if not (config.min_word_count <= verse.word_count <= config.max_word_count):
            dropped.append(
                DroppedVerse(verse.surah, verse.ayah, verse.text, "word_count")
            )
        else:
            kept.append(verse)

    # 2) full diacritization
    if config.require_full_diacritization:
        passing: list[Verse] = []
        for verse in kept:
            if arabic.diacritization_ratio(verse.text) >= config.diacritization_min_ratio:
                passing.append(verse)
            else:
                dropped.append(
                    DroppedVerse(verse.surah, verse.ayah, verse.text, "not_fully_diacritized")
                )
        kept = passing

    # 3) semantic duplicates (identical after marks are stripped)
    if config.dedupe_semantic_duplicates:
        seen: dict[str, tuple[int, int]] = {}
        unique: list[Verse] = []
        for verse in kept:
            # Skeleton with word boundaries preserved, so ayat that only differ
            # by diacritics (e.g. the Ar-Rahman refrain) collapse to one.
            key = " ".join(arabic.word_skeletons(verse.text))
            if key in seen:
                s0, a0 = seen[key]
                dropped.append(
                    DroppedVerse(
                        verse.surah,
                        verse.ayah,
                        verse.text,
                        f"duplicate_of_{s0}:{a0}",
                    )
                )
            else:
                seen[key] = (verse.surah, verse.ayah)
                unique.append(verse)
        kept = unique

    return kept, dropped
