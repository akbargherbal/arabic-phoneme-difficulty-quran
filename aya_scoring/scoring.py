"""Stages 2-5: letter weights, adjacency bonuses, tajweed boosters, normalization.

All thresholds/weights come from :class:`aya_scoring.config.ScoringConfig`; the
functions here contain no magic numbers.
"""

from __future__ import annotations

from . import arabic
from .config import ScoringConfig
from .filters import Verse
from .weights import NOON_RULE_TRIGGERS


def _adjacency_bonus(a: str, b: str, config: ScoringConfig) -> tuple[int, str | None]:
    """Bonus for one adjacent letter pair, plus which rule fired.

    Rules are mutually exclusive per pair and the strongest applies (spec §5b
    describes itself as "higher than" §5a, and the emphatic pair as the
    maximum bonus).
    """
    if config.is_emphatic_pair(a, b):
        return config.bonus_emphatic_pair, "emphatic"
    if config.share_group(a, b):
        return config.bonus_same_group, "same_group"
    if config.is_hard(a) and config.is_hard(b):
        return config.bonus_hard_adjacent, "hard_adjacent"
    return 0, None


def _word_clusters(word: str) -> list[tuple[str, set[int]]]:
    return list(arabic.letter_clusters(word))


def score_verse(verse: Verse, config: ScoringConfig) -> dict:
    text = verse.text
    letters = arabic.base_letters(text)
    word_clusters = [_word_clusters(w) for w in arabic.words(text)]

    # --- Stage 2: base letter weights (over every occurrence) ---------------
    base_score = sum(config.weight_of(ch) for ch in letters)
    hard_positions = [i for i, ch in enumerate(letters) if config.is_hard(ch)]
    hard_letter_count = len(hard_positions)
    distinct_hard_letters = ",".join(sorted({letters[i] for i in hard_positions}))

    # --- Stage 3: adjacency bonuses -----------------------------------------
    adjacency_bonus = 0
    hard_adjacent_count = 0
    same_group_count = 0
    emphatic_pair_count = 0

    def consider(a: str, b: str) -> None:
        nonlocal adjacency_bonus, hard_adjacent_count, same_group_count, emphatic_pair_count
        bonus, rule = _adjacency_bonus(a, b, config)
        if bonus == 0:
            return
        adjacency_bonus += bonus
        if rule == "emphatic":
            emphatic_pair_count += 1
            same_group_count += 1
        elif rule == "same_group":
            same_group_count += 1
        elif rule == "hard_adjacent":
            hard_adjacent_count += 1

    for clusters in word_clusters:
        seq = [ch for ch, _ in clusters]
        for a, b in zip(seq, seq[1:]):
            consider(a, b)

    if config.adjacency_across_words:
        prev_last: str | None = None
        for clusters in word_clusters:
            if not clusters:
                continue
            first = clusters[0][0]
            if prev_last is not None:
                consider(prev_last, first)
            prev_last = clusters[-1][0]

    # --- Stage 4: tajweed boosters ------------------------------------------
    tajweed_bonus = 0
    shadda_hard_count = 0
    madd_guttural_count = 0
    idgham_count = 0

    if config.enable_tajweed:
        for clusters in word_clusters:
            for idx, (letter, marks) in enumerate(clusters):
                if ord(arabic.SHADDA) in marks and config.is_hard(letter):
                    shadda_hard_count += 1
                if (ord(arabic.MADDAH) in marks
                        or ord(arabic.SUPERSCRIPT_ALEF) in marks):
                    neighbours = []
                    if idx > 0:
                        neighbours.append(clusters[idx - 1][0])
                    if idx + 1 < len(clusters):
                        neighbours.append(clusters[idx + 1][0])
                    # Spec §6 says "adjacent to a guttural", but its example
                    # (ٱلضَّآلِّينَ) neighbours the emphatic ض, so any hard
                    # (Tier 2/3) neighbour counts.
                    if any(config.is_hard(n) for n in neighbours):
                        madd_guttural_count += 1

        for prev, nxt in zip(word_clusters, word_clusters[1:]):
            if not prev or not nxt:
                continue
            last_letter, last_marks = prev[-1]
            has_tanween = bool(last_marks & arabic.TANWEEN_CODEPOINTS)
            if not has_tanween and last_letter in {"ا", "ي"} and len(prev) >= 2:
                has_tanween = bool(prev[-2][1] & arabic.TANWEEN_CODEPOINTS)
            noon_sakinah = last_letter == "ن" and arabic.SUKUN_CODEPOINTS & last_marks
            if (has_tanween or noon_sakinah) and nxt[0][0] in NOON_RULE_TRIGGERS:
                idgham_count += 1

        tajweed_bonus = (
            shadda_hard_count * config.bonus_shadda_hard
            + madd_guttural_count * config.bonus_madd_guttural
            + idgham_count * config.bonus_idgham
        )

    # --- Stage 5: normalization ---------------------------------------------
    raw_score = base_score + adjacency_bonus + tajweed_bonus
    letter_count = len(letters)
    if config.density_basis == "letter":
        density_score = raw_score / letter_count if letter_count else 0.0
    else:
        density_score = raw_score / verse.word_count if verse.word_count else 0.0
    density_per_letter = raw_score / letter_count if letter_count else 0.0

    return {
        "surah": verse.surah,
        "ayah": verse.ayah,
        "text": text,
        "word_count": verse.word_count,
        "letter_count": letter_count,
        "base_score": base_score,
        "hard_letter_count": hard_letter_count,
        "distinct_hard_letters": distinct_hard_letters,
        "adjacency_bonus": adjacency_bonus,
        "hard_adjacent_count": hard_adjacent_count,
        "same_group_count": same_group_count,
        "emphatic_pair_count": emphatic_pair_count,
        "tajweed_bonus": tajweed_bonus,
        "shadda_hard_count": shadda_hard_count,
        "madd_guttural_count": madd_guttural_count,
        "idgham_count": idgham_count,
        "raw_score": raw_score,
        "density_score": round(density_score, 6),
        "density_per_letter": round(density_per_letter, 6),
    }
