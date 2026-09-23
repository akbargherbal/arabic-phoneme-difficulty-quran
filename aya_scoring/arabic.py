"""Arabic script helpers: diacritics, normalization, tokens and words.

The Tanzil Uthmani text uses a number of Unicode codepoints that are not part of
the 28 canonical Arabic consonants (hamza carriers, dagger alef, small waw/yeh,
Quranic pause/annotation marks, tatweel ...).  Everything downstream of this
module works on the *base consonantal skeleton* produced by :func:`base_letters`,
so the scoring logic never has to know about the raw codepoints.
"""

from __future__ import annotations

import re

TATWEEL = "\u0640"

# --- Diacritics / combining marks -------------------------------------------
# Haraakat, shadda, sukun and the three tanween marks (U+064B..U+0652).
MARK_CODEPOINTS: set[int] = set(range(0x064B, 0x0653))
# Maddah, hamza above/below, subscript alef, inverted damma, etc.
MARK_CODEPOINTS |= {0x0653, 0x0654, 0x0655, 0x0656, 0x0657, 0x0658, 0x0659}
MARK_CODEPOINTS |= {0x065A, 0x065B, 0x065C, 0x065D, 0x065E, 0x065F}
# Dagger / superscript alef.
MARK_CODEPOINTS |= {0x0670}
# Quranic annotation signs (small high/low marks, sajdah, ...).
MARK_CODEPOINTS |= set(range(0x06D6, 0x06EE))
# Arabic Extended-A combining marks.
MARK_CODEPOINTS |= set(range(0x08F0, 0x0900))

# --- Quranic pause / annotation signs (U+06D6..U+06ED) ----------------------
# The block U+06D6..U+06ED is *not* uniformly pause marks.  The sets below only
# cover the small-high waqf signs and the pure annotation markers; the rest of
# the range carries pronunciation in the Uthmani script and is never removed by
# :func:`strip_pause_marks`.
#
# Default strip set: the waqf/pause signs U+06D6..U+06DC
#   (صلى، قلى، م، لا، ج، ∴ three-dots، س).
PAUSE_SIGN_CODEPOINTS: set[int] = set(range(0x06D6, 0x06DD))
# Pure non-pronunciation markers (opt-in): end-of-ayah, rub-el-hizb, sajdah.
NON_PRONUNCIATION_CODEPOINTS: set[int] = {0x06DD, 0x06DE, 0x06E9}
# Uthmani marks that carry pronunciation / madd and MUST survive:
#   U+06DF/U+06E0 silent-letter zeros, U+06E1 Uthmani sukun, U+06E2 iqlab meem,
#   U+06E3, U+06E4 small madda, U+06E5/U+06E6 small waw/yeh (hidden madd, e.g.
#   هُۥ), U+06E7/U+06E8 small high noon, U+06ED small low meem.  The basic
#   tashkeel range U+064B..U+065F and the dagger alef U+0670 also stay.
PROTECTED_PRONUNCIATION_CODEPOINTS: set[int] = (
    {0x06DF, 0x06E0, 0x06E1, 0x06E2, 0x06E3, 0x06E4,
     0x06E5, 0x06E6, 0x06E7, 0x06E8, 0x06ED}
    | {0x0670}
    | set(range(0x064B, 0x0660))
)
# Undecided: rounded / empty-centre high stops U+06EA..U+06EC.  Never stripped
# by default; the codepoint tally reports where they occur so the call can be
# made explicitly.
UNDECIDED_HIGH_STOP_CODEPOINTS: set[int] = {0x06EA, 0x06EB, 0x06EC}

# Convenience groups used by the tajweed stage.
SHADDA = "\u0651"
MADDAH = "\u0653"
SUPERSCRIPT_ALEF = "\u0670"
TANWEEN_CODEPOINTS = {0x064B, 0x064C, 0x064D}
HARAKAT_CODEPOINTS = {0x064E, 0x064F, 0x0650}
SUKUN_CODEPOINTS = {0x0652}

# --- Canonical base letters -------------------------------------------------
BASE_LETTERS: set[str] = set(
    "ابتثجحخدذرزسشصضطظعغفقكلمنهويء"
)

# Map non-canonical letters onto their canonical skeleton equivalent.
NORMALIZE_MAP: dict[str, str] = {
    "\u0622": "\u0627",  # ALEF WITH MADDA ABOVE      -> ا
    "\u0623": "\u0627",  # ALEF WITH HAMZA ABOVE      -> ا
    "\u0625": "\u0627",  # ALEF WITH HAMZA BELOW      -> ا
    "\u0671": "\u0627",  # ALEF WASLA                 -> ا
    "\u0672": "\u0627",  # ALEF WITH WAVY HAMZA ABOVE -> ا
    "\u0673": "\u0627",  # ALEF WITH WAVY HAMZA BELOW -> ا
    "\u0624": "\u0648",  # WAW WITH HAMZA ABOVE       -> و
    "\u0626": "\u064A",  # YEH WITH HAMZA ABOVE       -> ي
    "\u0649": "\u064A",  # ALEF MAKSURA               -> ي
    "\u0629": "\u062A",  # TEH MARBUTA                -> ت
}

# Raw codepoints that are long vowels / carriers and therefore legitimately
# appear without a haraka: alef, alef wasla, alef maksura, alef with madda,
# and the long-vowel waw/yeh.
MADD_EXEMPT: set[str] = {
    "\u0627",  # alef
    "\u0622",  # alef with madda
    "\u0671",  # alef wasla
    "\u0672",  # alef with wavy hamza above
    "\u0673",  # alef with wavy hamza below
    "\u0649",  # alef maksura
    "\u0648",  # waw (long vowel)
    "\u064A",  # yeh (long vowel)
}

_WHITESPACE_RE = re.compile(r"\s+")


def is_mark(ch: str) -> bool:
    return ord(ch) in MARK_CODEPOINTS


def is_base_letter(ch: str) -> bool:
    return ch in BASE_LETTERS


def normalize_letter(ch: str) -> str | None:
    """Return the canonical base letter for *ch*, or ``None`` if not a letter."""
    if ch in NORMALIZE_MAP:
        return NORMALIZE_MAP[ch]
    if ch in BASE_LETTERS:
        return ch
    return None


def strip_marks(text: str) -> str:
    """Remove all diacritics, Quranic marks and tatweel from *text*."""
    return "".join(
        ch
        for ch in text
        if ch != TATWEEL and ord(ch) not in MARK_CODEPOINTS
    )


def strip_pause_marks(text: str, *, include_non_pronunciation: bool = False) -> str:
    """Remove Quranic pause/annotation signs, preserving Uthmani orthography.

    Unlike :func:`strip_marks` this only deletes the small-high waqf signs
    (:data:`PAUSE_SIGN_CODEPOINTS`) and leaves every pronunciation-bearing mark
    untouched: the dagger alef (U+0670), the basic tashkeel range
    (U+064B..U+065F), the Uthmani sukun, the silent-letter zeros, the small
    waw/yeh hidden-madd marks, and the small high/low noon and low meem.

    The function is non-destructive: it returns a new string and never mutates
    its argument.  Whitespace is collapsed afterwards so a removed sign cannot
    leave double or stray spaces behind.

    Args:
        include_non_pronunciation: also drop the pure annotation markers in
            :data:`NON_PRONUNCIATION_CODEPOINTS` (end-of-ayah, rub-el-hizb and
            sajdah place).  Off by default.
    """
    strip = PAUSE_SIGN_CODEPOINTS
    if include_non_pronunciation:
        strip = strip | NON_PRONUNCIATION_CODEPOINTS
    kept = "".join(ch for ch in text if ord(ch) not in strip)
    return _WHITESPACE_RE.sub(" ", kept).strip()


def is_pause_sign(ch: str) -> bool:
    """True for a default-strip Quranic pause sign (U+06D6..U+06DC)."""
    return ord(ch) in PAUSE_SIGN_CODEPOINTS


def base_letters(text: str) -> list[str]:
    """Return the canonical consonantal skeleton of *text* as a list."""
    out: list[str] = []
    for ch in text:
        letter = normalize_letter(ch)
        if letter is not None:
            out.append(letter)
    return out


def base_skeleton(text: str) -> str:
    """Consonantal skeleton of *text* as a single string (for dedup keys)."""
    return "".join(base_letters(text))


def words(text: str) -> list[str]:
    """Whitespace split, dropping empty tokens."""
    return [w for w in _WHITESPACE_RE.split(text.strip()) if w]


def word_skeletons(text: str) -> list[str]:
    """Per-word consonantal skeletons (letters only)."""
    return [base_skeleton(w) for w in words(text)]


def letter_clusters(word: str):
    """Yield ``(base_letter, set_of_mark_codepoints)`` for one word.

    Marks are attached to the letter they follow, which mirrors how Arabic is
    written: a consonant is immediately followed by its harakat, shadda, ...
    """
    current: str | None = None
    marks: set[int] = set()
    for ch in word:
        if ch == TATWEEL:
            continue
        if ord(ch) in MARK_CODEPOINTS:
            marks.add(ord(ch))
            continue
        letter = normalize_letter(ch)
        if letter is not None:
            if current is not None:
                yield current, marks
            current, marks = letter, set()
        # any other character (non-Arabic) is ignored
    if current is not None:
        yield current, marks


def _word_clusters_raw(word: str) -> list[tuple[str, set[int]]]:
    """``[(raw_letter, marks), ...]`` for one word, preserving raw codepoints."""
    out: list[tuple[str, set[int]]] = []
    current: str | None = None
    marks: set[int] = set()
    for ch in word:
        if ch == TATWEEL:
            continue
        if ord(ch) in MARK_CODEPOINTS:
            marks.add(ord(ch))
            continue
        if normalize_letter(ch) is not None:
            if current is not None:
                out.append((current, marks))
            current, marks = ch, set()
        else:
            if current is not None:
                out.append((current, marks))
            current, marks = None, set()
    if current is not None:
        out.append((current, marks))
    return out


def diacritization_ratio(text: str) -> float:
    """Fraction of markable consonants that actually carry a mark.

    The spec asks for "every letter carries a diacritic".  Taken literally that
    is impossible for *any* Uthmani text: word-final consonants are routinely
    written without sukun (``مِن``, ``أَن``), long vowels (ا و ي) never carry a
    haraka, and assimilation hides marks (the first lam of the definite article
    in ``ٱللَّهِ``, the noon in ``كُنتُمْ``).  We therefore measure how
    vocalised the text is: exempting long-vowel carriers and word-final
    consonants, a fully diacritized text scores ~1.0 while a skeletal text
    (``simple-clean``) scores 0.0.  The caller picks the minimum acceptable
    ratio.
    """
    marked = total = 0
    for word in words(text):
        clusters = _word_clusters_raw(word)
        last = len(clusters) - 1
        for idx, (raw, marks) in enumerate(clusters):
            if raw in MADD_EXEMPT or idx == last:
                continue
            total += 1
            if marks:
                marked += 1
    return marked / total if total else 1.0


def is_fully_diacritized(text: str, min_ratio: float = 1.0) -> bool:
    """True when :func:`diacritization_ratio` is at least *min_ratio*."""
    return diacritization_ratio(text) >= min_ratio
