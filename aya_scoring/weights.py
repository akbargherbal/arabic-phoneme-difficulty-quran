"""Letter tiers, articulation groups and derived lookups (spec §4–§5).

The tables below are *defaults*.  They are plain data, so they can be overridden
from a JSON config file (see :class:`aya_scoring.config.ScoringConfig`) without
touching the scoring code.  These are the values we expect to iterate on.
"""

from __future__ import annotations

# --- Tier 0..3 base letter weights (spec §4) --------------------------------
DEFAULT_LETTER_WEIGHTS: dict[str, int] = {
    # Tier 0 - easy (near-universal articulations)
    "ا": 0, "ب": 0, "م": 0, "ت": 0, "د": 0, "ن": 0, "و": 0, "ي": 0, "ل": 0,
    # Tier 1 - moderate
    "س": 1, "ز": 1, "ف": 1, "ك": 1, "ج": 1, "ش": 1,
    # Tier 2 - hard / guttural (pharyngeal, uvular, glottal)
    "ء": 2, "ه": 2, "ح": 3, "خ": 3, "ع": 3, "غ": 3, "ق": 3,
    # Tier 3 - hardest: emphatic & interdental
    "ث": 3, "ذ": 3, "ص": 3, "ط": 3, "ض": 4, "ظ": 4,
}

# Threshold at / above which a letter counts as "hard" (Tier 2 or Tier 3).
DEFAULT_HARD_THRESHOLD = 2

# --- Articulation groups (spec §5b) -----------------------------------------
DEFAULT_ARTICULATION_GROUPS: dict[str, set[str]] = {
    "labial": {"ب", "م", "و"},
    "dental_stops": {"ت", "د", "ط"},
    "interdental": {"ث", "ذ", "ظ"},
    "sibilant": {"س", "ز", "ص"},
    "guttural": {"ح", "خ", "ع", "غ", "ق", "ء", "ه"},
    "liquid_nasal": {"ل", "ر", "ن"},
}

# Emphatic <-> plain counterpart pairs (spec §5b "extra boost").
DEFAULT_EMPHATIC_PAIRS: set[frozenset[str]] = {
    frozenset({"ط", "ت"}),
    frozenset({"ظ", "ذ"}),
    frozenset({"ص", "س"}),
    frozenset({"ض", "د"}),
}

# --- Adjacency bonus values (spec §5) ---------------------------------------
DEFAULT_BONUS_HARD_ADJACENT = 2      # §5a: two Tier 2/3 letters side by side
DEFAULT_BONUS_SAME_GROUP = 3          # §5b: same articulation group
DEFAULT_BONUS_EMPHATIC_PAIR = 4       # §5b extra boost: emphatic/plain pair

# --- Tajweed bonus values (spec §6) -----------------------------------------
DEFAULT_BONUS_SHADDA_HARD = 1         # shadda on a Tier 2/3 letter
DEFAULT_BONUS_MADD_GUTTURAL = 1       # long madd next to a guttural
DEFAULT_BONUS_IDGHAM = 1              # idgham/ikhfa trigger at word boundary

# Idgham / ikhfa / iqlab trigger letters (noon-sakinah & tanween followers).
# I.e. every consonant except the long-vowel alef.
NOON_RULE_TRIGGERS: set[str] = set("بتثجحخدذرزسشصضطظعغفقكلمنهويء")
