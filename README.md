# Ayah Difficulty Scoring

Score Quran ayat as candidates for an Arabic pronunciation-training dataset.

This is a **text-only** pipeline. It does not touch audio: for every ayah whose
word count falls in a configurable range, it applies hard filters and computes
difficulty scores (base letter weight, adjacency bonuses, optional tajweed
boosters, raw and density scores). The output is a complete scored table — not a
hand-picked shortlist. Final selection is done separately by sorting/filtering
the output.

The full method is described in
[`quran_ayah_difficulty_scoring_spec.md`](quran_ayah_difficulty_scoring_spec.md).

## Requirements

- Python 3.10+
- `requests` (corpus download), `pandas` (only for optional Parquet output;
  CSV and JSON need no third-party packages)

## 1. Download the corpus

[`download_corpus.py`](download_corpus.py) fetches the Quran text from
[Tanzil](https://tanzil.net) in six scripts:

| Script | Description |
|---|---|
| `uthmani` | Uthmani text as in the Madina Mushaf (primary source) |
| `uthmani-min` | Uthmani with minimal diacritics/symbols |
| `simple` | Simple (Imla'ei) text, fully diacritized |
| `simple-plain` | Simple without ikhfa/idgham demonstration |
| `simple-min` | Simple with minimal diacritics |
| `simple-clean` | Simple without any diacritics (skeleton only) |

```bash
python download_corpus.py                       # all scripts
python download_corpus.py --scripts uthmani simple
```

Output:

```
data/raw/tanzil/<script>.txt     verbatim download (incl. licence header)
data/quran/<script>.json         [{surah, ayah, text}, ...]
data/quran/<script>.csv
data/quran/manifest.json         provenance + counts
```

The basmala that Tanzil prepends to the first ayah of surahs 2–114 (except
At-Tawba) is stripped, so word counts reflect the actual ayah.

## 2. Score the ayat

```bash
python -m aya_scoring                       # uthmani, 3-12 words, tajweed on
python -m aya_scoring --script simple
python -m aya_scoring --min-word-count 4 --max-word-count 8 --sort-by density
python -m aya_scoring --config my_config.json
python -m aya_scoring --formats csv,json,parquet
```

Key options:

| Flag | Default | Meaning |
|---|---|---|
| `--corpus` | – | Path to a corpus JSON (overrides `--script`) |
| `--script` | `uthmani` | Corpus under `data/quran/<script>.json` |
| `--min-word-count` / `--max-word-count` | `3` / `12` | Length range (inclusive) |
| `--no-tajweed` | off | Disable Stage 4 |
| `--adjacency-across-words` | off | Allow adjacency across word boundaries |
| `--density-basis` | `word` | `word` or `letter` |
| `--config` | – | JSON overriding any `ScoringConfig` field |
| `--out-dir` | `output` | Output directory |
| `--formats` | `csv,json` | Any of `csv,json,parquet` |
| `--sort-by` | `raw` | `raw` or `density` |
| `--top` | `10` | Preview rows (`0` = none) |

Output files:

```
output/ayah_scores.csv       one row per qualifying ayah + all subscores
output/ayah_scores.json
output/dropped_ayahs.csv     filtered ayat with the reason they were dropped
output/scoring_config.json   exact config used for the run
```

## Scoring pipeline

1. **Hard filters** ([`filters.py`](aya_scoring/filters.py)) — word count in
   range, full diacritization, semantic dedup (identical after diacritics are
   stripped, e.g. the Ar-Rahman refrain).
2. **Base letter weights** ([`weights.py`](aya_scoring/weights.py)) — sum over
   every letter occurrence. Tiers: easy `ابمت دونيل` = 0, moderate `سزفكجش` = 1,
   guttural `ءه` = 2 / `حخعغق` = 3, emphatic & interdental `ثذصط` = 3 with
   `ضظ` = 4.
3. **Adjacency bonuses** ([`scoring.py`](aya_scoring/scoring.py)) — strongest
   rule per adjacent pair: two hard letters **+2**, same articulation group
   **+3**, emphatic/plain pair (ط/ت, ظ/ذ, ص/س, ض/د) **+4**.
4. **Tajweed boosters** (optional) — shadda on a hard letter **+1**, madd next
   to a hard letter **+1**, idgham/ikhfa trigger at a word boundary **+1**.
5. **Normalization** — `raw_score = base + adjacency + tajweed`, and
   `density_score = raw_score / word_count`. Sorting by raw alone favors long
   ayat; density surfaces short, phonetically packed ones. Both are in the
   output so the final blend can be decided downstream.

### Output columns

`surah, ayah, text, word_count, letter_count, base_score, hard_letter_count,
distinct_hard_letters, adjacency_bonus, hard_adjacent_count, same_group_count,
emphatic_pair_count, tajweed_bonus, shadda_hard_count, madd_guttural_count,
idgham_count, raw_score, density_score, density_per_letter`

## Configuration

Every weight, bonus and threshold lives in
[`aya_scoring/config.py`](aya_scoring/config.py) (`ScoringConfig`) as data, not
inline magic numbers. Override any subset with a JSON file:

```json
{
  "min_word_count": 4,
  "max_word_count": 8,
  "letter_weights": {"ض": 5, "ظ": 5},
  "bonus_emphatic_pair": 5
}
```

```bash
python -m aya_scoring --config my_config.json
```

The exact config used for a run is recorded in `output/scoring_config.json`.

## Tests

```bash
python -m unittest discover -s tests
```

## Notes / deviations from the spec

- **Diacritization filter.** The literal rule "every letter carries a
  diacritic" rejects *all* valid Uthmani text: word-final consonants are written
  without sukun (`مِن`, `أَن`), long vowels (ا/و/ي) never carry a haraka, and
  assimilation hides marks (the first lam of `ٱللَّهِ`, the noon in `كُنتُمْ`).
  The pipeline instead measures a diacritization **ratio** (default
  `diacritization_min_ratio = 0.7`): skeletal text scores 0.0, Uthmani ≥ 0.71.
- **Madd booster.** Spec §6 says "madd adjacent to a guttural", but its own
  example `ٱلضَّآلِّينَ` neighbours the emphatic ض, so any Tier 2/3 neighbour
  counts.
- **Letter normalization.** Non-canonical forms (`أ إ آ ٱ` → `ا`, `ؤ` → `و`,
  `ئ ى` → `ي`, `ة` → `ت`) are folded to the base skeleton; standalone hamza
  `ء` stays a Tier 2 letter.
- **Parquet** output is skipped (with a warning) unless `pyarrow` or
  `fastparquet` is installed.

## Corpus attribution

Quran text © [Tanzil Project](https://tanzil.net), licensed under
[CC BY 3.0](https://creativecommons.org/licenses/by/3.0/). Verbatim copies
including the licence header are kept in `data/raw/tanzil/`.
