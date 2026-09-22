# Ayah Difficulty Scoring Spec
### For scoring Quran ayah text as candidates for an Arabic pronunciation training dataset

---

## 1. Goal

This task is **text-only**. The agent does **not** download, play, transcribe, or otherwise handle any audio file. Audio files already exist (one mp3 per ayah); the agent's job is limited to processing the **Quran text** and producing a **scored dataset**.

For every ayah whose word count falls within a configurable length range (see §1.1), compute:

1. Whether it passes the hard filters (§3), and
2. Its difficulty score(s) — base letter weight, adjacency bonuses, raw score, density score (§4–§7).

**Output:** a data file (e.g. CSV/JSON/Parquet) with one row per qualifying ayah, containing `surah`, `ayah`, `text`, `word_count`, and all computed scores/sub-scores — **not** a final hand-picked shortlist. The final decision on which ayat to actually use for training is made separately afterward, by us, once we can inspect and sort this scored output. The agent's deliverable ends at producing this complete scored table.

This document defines the scoring pipeline: hard filters, letter weighting, adjacency bonuses, and normalization. **No code — this is a spec for an implementation agent.**

### 1.1 Configurable parameters (do not hardcode)

The word-count bounds are a first guess and will likely be tuned after seeing results. The agent must expose them as parameters/variables, not literals baked into the logic:

| Parameter | Initial value | Notes |
|---|---|---|
| `MIN_WORD_COUNT` | 3 | Lower bound, inclusive |
| `MAX_WORD_COUNT` | 12 | Upper bound, inclusive |

Ideally these are passed as function arguments / config values / CLI flags, so the whole pipeline can be re-run with different bounds without touching the scoring logic. The same applies to any other threshold introduced later (e.g. letter-tier weights, bonus values in §5–§6) — keep them as named, adjustable constants/config rather than inline magic numbers, since we will likely iterate on these too.

---

## 2. Recommended Data Sources

| Source | What it provides | Why use it |
|---|---|---|
| **Tanzil.net** | Fully diacritized Uthmani text, indexed by surah/ayah, CSV/XML | Primary source — required for any letter-weight analysis. This is the only source strictly needed for this task |
| **Quran.com API / quran-json** (GitHub) | JSON with `surah`, `ayah`, `text_uthmani`, `text_simple` | Alternative/backup text source, easy programmatic ingestion |
| **Quranic Arabic Corpus** (corpus.quran.com) | Morphological/phonetic tagging per word | Optional — useful for validating tajweed-based difficulty later |

**Note on audio:** matching scored ayat to their existing mp3 files (e.g. via the EveryAyah `SSSAAA.mp3` naming convention) is a **separate, later step** handled by us once we've decided which ayat to keep. The agent only needs `surah` and `ayah` numbers in its output so that join is possible afterward — it does not need to touch, verify, or reference any audio file itself.

---

## 3. Stage 1 — Hard Filters (pass/fail, no scoring yet)

Apply these **before** any difficulty scoring. Ayat failing any filter are dropped entirely.

| Filter | Rule |
|---|---|
| Word count | `MIN_WORD_COUNT ≤ word_count ≤ MAX_WORD_COUNT` (count after stripping diacritics; bounds are parameters, see §1.1) |
| Full diacritization | Every letter must carry a diacritic (fatha/damma/kasra/sukun/shadda); drop ayat with missing marks |
| Semantic duplicates | Deduplicate ayat that are textually identical after diacritics are stripped (e.g. the repeated refrain in Surah Ar-Rahman, or repeated verses in Al-Mursalat) |

Only ayat that pass all three move to scoring.

---

## 4. Stage 2 — Base Letter Weights

**Principle:** the rarer a letter's articulation point/manner is across world languages, the higher its weight. Emphatic (mufakhkham) and interdental letters score highest, since these are the most common source of mispronunciation.

| Tier | Letters | Weight | Rationale |
|---|---|---|---|
| 0 — Easy (near-universal) | ا ب م ت د ن و ي ل | 0 | Articulation shared by most world languages |
| 1 — Moderate | س ز ف ك ج ش | 1 | Common cross-linguistically but with minor Arabic-specific variation |
| 2 — Hard / guttural | ء ه ح خ ع غ ق | 2–3 | Pharyngeal/uvular articulation, rare outside Arabic (ق especially — a true uvular stop) |
| 3 — Hardest: emphatic & interdental | ض ص ط ظ ث ذ | 3–4 | Require tongue-pharynx constriction (tafkhim) or an inter-dental point absent from most languages; primary source of learner error |

> Suggestion: weight **ض** and **ظ** at the very top of the scale (4) — they are the rarest cross-linguistically and the most confused even among Arabic learners.

**Ayah base score = sum of the weights of every letter in the ayah** (diacritics stripped first). Decide up front whether to sum over *all letter occurrences* (rewards repetition) or over the *set of distinct hard letters present* (rewards variety) — see §7 for how to balance both.

---

## 5. Stage 3 — Adjacency Bonuses

This is the key refinement: **two difficult letters sitting next to each other inside the same word create more articulatory difficulty than the sum of their individual weights**, because the tongue/lips must transition rapidly between two demanding positions.

### 5a. Any two hard letters adjacent
Two letters both from Tier 2 or Tier 3, directly adjacent (no letter between them) → **+2 bonus points**, on top of their individual weights.

### 5b. Adjacent letters sharing a *similar articulation point* (the most important rule)
This captures your example (e.g. م immediately followed by ب): **even letters that aren't individually "hard" become difficult when their neighbor shares a very close articulation point**, because the mouth must move precisely between two close places of articulation in a very short time.

| Articulation group | Letters | Example of doubled difficulty |
|---|---|---|
| Labial | ب م و | م directly followed by ب (your example) |
| Dental/alveolar stops | ت د ط | Emphatic vs. plain stop confusion |
| Interdental | ث ذ ظ | Same point, differ only in voicing/emphasis — very fine distinction |
| Sibilant | س ز ص | Plain vs. emphatic sibilant distinction |
| Deep guttural/uvular | ح خ ع غ ق ء ه | Consecutive "throat" articulations |
| Liquid/nasal | ل ر ن | Frequent site of tajweed assimilation rules (idgham/ikhfa) |

**Rule:** two adjacent letters from the same group → **+3 bonus points** (higher than the generic hard-letter adjacency in 5a, since this is a precise motor-control challenge, not just individual difficulty).

**Extra boost:** if one of the two adjacent letters is Tier 3 (ض/ظ/ص/ط/ث/ذ) and the other is its non-emphatic counterpart from the same group (e.g. ط/ت, or ظ/ذ) → **maximum bonus, +4**, since this pairing is precisely the most common real-world error pattern for non-native and beginner speakers.

---

## 6. Stage 4 — Optional Tajweed-Based Boosters

These add confidence that "difficulty" is phonetically real, not just an artifact of letter counting:

| Signal | Bonus |
|---|---|
| Shadda (gemination) on a Tier 2/3 letter (e.g. ضّ in الضَّالِّينَ) | +1 |
| Long madd adjacent to a guttural letter (e.g. اَلضَّآلِّينَ) | +1 |
| Idgham/ikhfa trigger at a word boundary (sukun-noon or tanween before an idgham/ikhfa letter) | +1 per occurrence |

This stage benefits from referencing the Quranic Arabic Corpus's phonetic/morphological tags rather than re-deriving tajweed rules from scratch via regex.

---

## 7. Stage 5 — Normalization (avoid length bias)

**Problem:** a 12-word ayah will almost always outscore a 3-word ayah simply by having more letters. To correct for this, compute two parallel scores per ayah:

1. **Raw score** = sum of all base weights + all adjacency/tajweed bonuses
2. **Density score** = raw score ÷ word count (or ÷ letter count)

Select the final set using a **blend of both** — e.g., ~70% of selections ranked by density score, ~30% by raw score — so that short, letter-dense ayat (3–5 words but phonetically packed) aren't crowded out by longer, moderately-difficult ayat that only win on volume.

---

## 8. Out of Scope for This Task (handled by us afterward, not by the agent)

The following are **not** part of the agent's deliverable. They're listed here only so the agent's output is shaped to make them easy for us to do later:

- **Final selection / shortlisting** — deciding which ayat actually go into the training set. The agent's job stops at producing the full scored table for every ayah in the length range; it should not pre-filter down to a "final" subset.
- **Bucketing by score tier, surah diversification, root/word repetition capping** — these are sampling decisions we'll make once we can see the scored data (e.g. by sorting/filtering the output ourselves).
- **Human spot-checking of individual ayat.**
- **Matching to audio files** — see the note in §2.

To make those later steps easy, the output table (§1) should include enough columns to sort and filter by: at minimum `surah`, `ayah`, `text`, `word_count`, `raw_score`, `density_score`, and ideally the individual bonus components broken out (e.g. `adjacency_bonus`, `tajweed_bonus` if §6 is implemented) rather than only a single final number.

---

## 9. Pipeline Summary (agent's actual scope)

```
Raw Quran text (Tanzil), with configurable MIN/MAX_WORD_COUNT
        │
        ▼
Stage 1: Hard filters (word count within configured range, full diacritics, dedup)
        │
        ▼
Stage 2: Base letter weight sum (per ayah)
        │
        ▼
Stage 3: Adjacency bonuses (hard-hard, same-articulation-group, emphatic/plain pair)
        │
        ▼
Stage 4 (optional): Tajweed boosters (shadda, madd, idgham/ikhfa)
        │
        ▼
Stage 5: Normalize → raw score + density score
        │
        ▼
OUTPUT: full scored table (surah, ayah, text, word_count, all scores)
        │
        ▼
────────────── handed off to us ──────────────
        │
        ▼
(Later, by us) Bucketing / diversity sampling / spot-check / audio matching / final selection
```
