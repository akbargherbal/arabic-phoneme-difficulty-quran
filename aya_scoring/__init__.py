"""Quran ayah difficulty scoring pipeline.

Implements the spec in ``quran_ayah_difficulty_scoring_spec.md``.
"""

from .config import ScoringConfig
from .pipeline import score_verses, load_corpus

__all__ = ["ScoringConfig", "score_verses", "load_corpus"]
