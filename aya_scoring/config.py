"""Configurable parameters for the scoring pipeline (spec §1.1).

Nothing in the scoring logic hardcodes a threshold: every knob lives here with a
documented default and can be overridden from a JSON file via
:meth:`ScoringConfig.from_json`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, fields

from . import weights as w


@dataclass
class ScoringConfig:
    # --- Stage 1: hard filters (§1.1, §3) -----------------------------------
    min_word_count: int = 3
    max_word_count: int = 12
    require_full_diacritization: bool = True
    # Minimum fraction of markable consonants that must carry a diacritic.
    # Uthmani scores >=0.71; skeletal (undiacritized) text scores 0.0.
    diacritization_min_ratio: float = 0.7
    dedupe_semantic_duplicates: bool = True

    # --- Stage 2: base letter weights (§4) ----------------------------------
    letter_weights: dict[str, int] = field(
        default_factory=lambda: dict(w.DEFAULT_LETTER_WEIGHTS)
    )
    hard_threshold: int = w.DEFAULT_HARD_THRESHOLD

    # --- Stage 3: adjacency bonuses (§5) ------------------------------------
    articulation_groups: dict[str, set[str]] = field(
        default_factory=lambda: {k: set(v) for k, v in w.DEFAULT_ARTICULATION_GROUPS.items()}
    )
    emphatic_pairs: set[frozenset[str]] = field(
        default_factory=lambda: set(w.DEFAULT_EMPHATIC_PAIRS)
    )
    bonus_hard_adjacent: int = w.DEFAULT_BONUS_HARD_ADJACENT
    bonus_same_group: int = w.DEFAULT_BONUS_SAME_GROUP
    bonus_emphatic_pair: int = w.DEFAULT_BONUS_EMPHATIC_PAIR
    # §5a talks about "inside the same word"; keep adjacency within words by
    # default, but allow crossing word boundaries as an experiment.
    adjacency_across_words: bool = False

    # --- Stage 4: optional tajweed boosters (§6) ----------------------------
    enable_tajweed: bool = True
    bonus_shadda_hard: int = w.DEFAULT_BONUS_SHADDA_HARD
    bonus_madd_guttural: int = w.DEFAULT_BONUS_MADD_GUTTURAL
    bonus_idgham: int = w.DEFAULT_BONUS_IDGHAM

    # --- Stage 5: normalization (§7) ----------------------------------------
    density_basis: str = "word"  # "word" or "letter"

    # ------------------------------------------------------------------ #
    def to_json_dict(self) -> dict:
        out: dict = {}
        for f in fields(self):
            value = getattr(self, f.name)
            if f.name == "emphatic_pairs":
                value = sorted(sorted(pair) for pair in value)
            elif isinstance(value, set):
                value = sorted(value)
            elif isinstance(value, dict):
                value = {k: sorted(v) if isinstance(v, set) else v
                         for k, v in value.items()}
            out[f.name] = value
        return out

    @classmethod
    def from_json(cls, path: str) -> "ScoringConfig":
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        cfg = cls()
        for key, value in data.items():
            if not hasattr(cfg, key):
                raise ValueError(f"Unknown config key: {key!r}")
            if key == "articulation_groups":
                value = {k: set(v) for k, v in value.items()}
            elif key == "emphatic_pairs":
                value = {frozenset(pair) for pair in value}
            setattr(cfg, key, value)
        return cfg

    # ------------------------------------------------------------------ #
    def weight_of(self, letter: str) -> int:
        return self.letter_weights.get(letter, 0)

    def is_hard(self, letter: str) -> bool:
        return self.weight_of(letter) >= self.hard_threshold

    def group_memberships(self, letter: str) -> set[str]:
        return {name for name, members in self.articulation_groups.items()
                if letter in members}

    def share_group(self, a: str, b: str) -> str | None:
        shared = self.group_memberships(a) & self.group_memberships(b)
        if not shared:
            return None
        # deterministic pick: first by sorted group name
        return sorted(shared)[0]

    def is_emphatic_pair(self, a: str, b: str) -> bool:
        return frozenset({a, b}) in self.emphatic_pairs
