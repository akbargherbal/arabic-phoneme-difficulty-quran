"""Tests for the narrow Quranic pause-mark helper.

These cover the protected-vs-stripped contract for U+06D6..U+06ED.  They are
written in plain pytest style (and are also discoverable by unittest).
"""

import pytest

from aya_scoring import arabic

# Default strip set: the waqf signs U+06D6..U+06DC.
PAUSE_SIGNS = sorted(arabic.PAUSE_SIGN_CODEPOINTS)
# Pure annotation markers, opt-in only.
NON_PRONUNCIATION = sorted(arabic.NON_PRONUNCIATION_CODEPOINTS)
# Uthmani marks that carry pronunciation and must always survive.
PROTECTED = sorted(
    {0x06DF, 0x06E0, 0x06E1, 0x06E2, 0x06E3, 0x06E4,
     0x06E5, 0x06E6, 0x06E7, 0x06E8, 0x06ED, 0x0670}
    | set(range(0x064B, 0x0660))
)
UNDECIDED = sorted(arabic.UNDECIDED_HIGH_STOP_CODEPOINTS)


@pytest.mark.parametrize("cp", PAUSE_SIGNS)
def test_default_sign_is_removed(cp):
    text = "قَالَ" + chr(cp) + " بَلَى"
    out = arabic.strip_pause_marks(text)
    assert chr(cp) not in out
    assert out == "قَالَ بَلَى"


@pytest.mark.parametrize("cp", NON_PRONUNCIATION)
def test_non_pronunciation_survives_by_default(cp):
    out = arabic.strip_pause_marks("عَمَّا" + chr(cp) + " يَتَسَاءَلُونَ")
    assert chr(cp) in out


@pytest.mark.parametrize("cp", NON_PRONUNCIATION)
def test_non_pronunciation_stripped_when_opted_in(cp):
    out = arabic.strip_pause_marks(
        "عَمَّا" + chr(cp) + " يَتَسَاءَلُونَ", include_non_pronunciation=True
    )
    assert chr(cp) not in out
    assert out == "عَمَّا يَتَسَاءَلُونَ"


@pytest.mark.parametrize("cp", PROTECTED + UNDECIDED)
def test_protected_marks_always_survive(cp):
    text = "بِسْمِ" + chr(cp) + " ٱللَّهِ"
    # Even the most aggressive mode must not touch pronunciation marks, and the
    # undecided high stops are off by default.
    assert arabic.strip_pause_marks(text, include_non_pronunciation=True) == text


def test_strip_marks_unchanged_contract():
    # The broad helper still removes everything the scoring skeleton needs.
    assert arabic.strip_marks("قَالَ") == "قال"
    assert arabic.strip_marks("هَٰذَا") == "هذا"


def test_dagger_alef_survives():
    assert "\u0670" in "هَٰذَا"
    assert arabic.strip_pause_marks("هَٰذَا") == "هَٰذَا"
    assert arabic.strip_pause_marks("ذَٰلِكَ") == "ذَٰلِكَ"


def test_small_waw_hidden_madd_survives():
    text = "عَلَيْهِۥ"  # contains U+06E5 SMALL WAW
    assert "\u06e5" in text
    assert arabic.strip_pause_marks(text) == text


def test_real_ayah_three_dots_and_dagger_alef():
    # 2:2 contains two U+06DB (∴) signs and a dagger alef in ذَٰلِكَ.
    text = "ذَٰلِكَ ٱلْكِتَـٰبُ لَا رَيْبَ ۛ فِيهِ ۛ هُدًى لِّلْمُتَّقِينَ"
    out = arabic.strip_pause_marks(text)
    assert "\u06db" not in out
    assert "\u0670" in out
    assert out == "ذَٰلِكَ ٱلْكِتَـٰبُ لَا رَيْبَ فِيهِ هُدًى لِّلْمُتَّقِينَ"
    assert "  " not in out


def test_real_ayah_jeem_and_sad_literal_signs():
    # 2:7 contains U+06D6 (صلى) and 2:13 contains U+06D7 (قلى); U+06DA is ج.
    salaa = "خَتَمَ ٱللَّهُ عَلَىٰ قُلُوبِهِمْ ۖ وَعَلَىٰ سَمْعِهِمْ"
    qala = "قَالُوٓا۟ أَنُؤْمِنُ ۗ أَلَا"
    jeem = "وَٱلْأَرْضِ ۚ قُلْ"
    for text in (salaa, qala, jeem):
        out = arabic.strip_pause_marks(text)
        assert "\u06d6" not in out
        assert "\u06d7" not in out
        assert "\u06da" not in out
        assert "  " not in out
        assert not out.endswith(" ")
        # Nothing else changed: re-adding the signs is out of scope, but the
        # stripped result must be the input with just the sign(s) gone.
        assert out == " ".join(
            w for w in text.split() if w not in {"\u06d6", "\u06d7", "\u06da"}
        )


def test_input_not_mutated():
    text = "قَالَ ۖ بَلَى"
    _ = arabic.strip_pause_marks(text)
    assert text == "قَالَ ۖ بَلَى"


def test_whitespace_clean_when_sign_is_standalone():
    assert arabic.strip_pause_marks("بَلَى ۖ قَالَ") == "بَلَى قَالَ"
    assert arabic.strip_pause_marks("  بَلَى ۖ قَالَ  ") == "بَلَى قَالَ"
