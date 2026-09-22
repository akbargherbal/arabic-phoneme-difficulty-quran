import unittest

from aya_scoring import arabic
from aya_scoring.config import ScoringConfig
from aya_scoring.filters import Verse, apply_hard_filters
from aya_scoring.scoring import score_verse


def sv(text):
    return Verse(surah=1, ayah=1, text=text, word_count=len(text.split()))


class TestArabic(unittest.TestCase):
    def test_normalization(self):
        self.assertEqual(arabic.base_skeleton("أَنَّ"), "ان")
        self.assertEqual(arabic.base_skeleton("ٱللَّهِ"), "الله")
        self.assertEqual(arabic.base_skeleton("رَحْمَةً"), "رحمت")
        self.assertEqual(arabic.base_skeleton("يُؤْمِنُونَ"), "يومنون")
        self.assertEqual(arabic.base_skeleton("شَىْءٍ"), "شيء")

    def test_marks_stripped(self):
        self.assertEqual(arabic.strip_marks("قَالَ"), "قال")

    def test_diacritization_ratio(self):
        self.assertEqual(arabic.diacritization_ratio("بسم الله"), 0.0)
        self.assertGreaterEqual(arabic.diacritization_ratio("بِسْمِ ٱللَّهِ"), 0.7)


class TestAdjacency(unittest.TestCase):
    def setUp(self):
        self.cfg = ScoringConfig(enable_tajweed=False)

    def test_emphatic_pair_max_bonus(self):
        row = score_verse(sv("طَتَ"), self.cfg)
        self.assertEqual(row["adjacency_bonus"], 4)
        self.assertEqual(row["emphatic_pair_count"], 1)

    def test_same_group_bonus(self):
        row = score_verse(sv("بَمَ"), self.cfg)
        self.assertEqual(row["adjacency_bonus"], 3)
        self.assertEqual(row["same_group_count"], 1)

    def test_hard_adjacent_bonus(self):
        row = score_verse(sv("خَطَ"), self.cfg)
        self.assertEqual(row["adjacency_bonus"], 2)
        self.assertEqual(row["hard_adjacent_count"], 1)

    def test_no_bonus(self):
        row = score_verse(sv("بَتَ"), self.cfg)
        self.assertEqual(row["adjacency_bonus"], 0)

    def test_adjacency_does_not_cross_words_by_default(self):
        row = score_verse(sv("طَ تَ"), self.cfg)
        self.assertEqual(row["adjacency_bonus"], 0)

    def test_adjacency_across_words_when_enabled(self):
        cfg = ScoringConfig(enable_tajweed=False, adjacency_across_words=True)
        row = score_verse(sv("طَ تَ"), cfg)
        self.assertEqual(row["adjacency_bonus"], 4)


class TestWeightsAndDensity(unittest.TestCase):
    def test_base_score_sums_occurrences(self):
        cfg = ScoringConfig(enable_tajweed=False)
        row = score_verse(sv("ضَضَ"), cfg)
        self.assertEqual(row["base_score"], 8)
        self.assertEqual(row["raw_score"], 8 + 2)  # ض/ض hard adjacency

    def test_density_score(self):
        cfg = ScoringConfig(enable_tajweed=False)
        row = score_verse(sv("ضَضْ بَتَ"), cfg)
        self.assertAlmostEqual(row["density_score"], row["raw_score"] / 2)


class TestTajweed(unittest.TestCase):
    def test_shadda_on_hard_letter(self):
        cfg = ScoringConfig()
        row = score_verse(sv("ضَّالِّينَ"), cfg)
        self.assertGreaterEqual(row["shadda_hard_count"], 1)
        self.assertGreaterEqual(row["tajweed_bonus"], 1)

    def test_idgham_at_word_boundary(self):
        cfg = ScoringConfig()
        row = score_verse(sv("مِنْ مَاءٍ"), cfg)
        self.assertEqual(row["idgham_count"], 1)


class TestFilters(unittest.TestCase):
    def test_word_count_bounds(self):
        cfg = ScoringConfig(min_word_count=3, max_word_count=4)
        verses = [
            Verse(1, 1, "بِسْمِ ٱللَّهِ", 2),
            Verse(1, 2, "بِسْمِ ٱللَّهِ ٱلرَّحْمَٰنِ", 3),
        ]
        kept, dropped = apply_hard_filters(verses, cfg)
        self.assertEqual([v.surah for v in kept], [1])
        self.assertEqual(dropped[0].reason, "word_count")

    def test_dedupe(self):
        cfg = ScoringConfig(min_word_count=1, max_word_count=10)
        verses = [
            Verse(55, 1, "فَبِأَىِّ ءَالَآءِ رَبِّكُمَا تُكَذِّبَانِ", 4),
            Verse(55, 2, "فَبِأَىِّ ءَالَآءِ رَبِّكُمَا تُكَذِّبَانِ", 4),
        ]
        kept, dropped = apply_hard_filters(verses, cfg)
        self.assertEqual(len(kept), 1)
        self.assertIn("duplicate", dropped[0].reason)


if __name__ == "__main__":
    unittest.main()
