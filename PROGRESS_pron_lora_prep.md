# Pronunciation-LoRA prep — progress handoff

Read this first when resuming work on branch `pron-lora-prep`. It records what
was done, where everything lives, and the gotchas that repeatedly bit us.
Last updated: 2026-09-23 (session 4).

Scope so far: **no training, no model repos, no merges.** Work has been (a) a
weighted text scorer, (b) a 40-ayah pronunciation shortlist + caption files
split across uthmani/simple script, and (c) an audit of the donor recitation
audio bucket in GCS. Nothing here trains a model yet.

## Branch / commits

Branch: `pron-lora-prep` (pushed to `origin`). Commits, newest last:

| commit | summary |
|---|---|
| `aebaf26` | weighted scorer, pause-mark helper, dual-script join (Task 1) |
| `e779626` | re-cut shortlist to 4–12 words with A/B types; captions; donor-audio audit (Task 2) |
| `816628b` | reciter style classified via common-knowledge fallback + `classification_source` (Task 3, gap 1) |
| `852c6fe` | committed ffprobe results for the Part C scratch download (Task 3, gap 2) |

PR link (branch): https://github.com/akbargherbal/arabic-phoneme-difficulty-quran/pull/new/pron-lora-prep

## Reproduce / re-run

```bash
# full test suite (112 tests currently pass)
python -m pytest tests/ -q

# score all ayat for pronunciation difficulty (min 4 / max 12 words, density sort)
python -m aya_scoring --config configs/pron_hkhad.json \
    --min-word-count 4 --max-word-count 12 \
    --sort-by density --out-dir data/pron/v2
# produced data/pron/v2/ayah_scores.csv (2911 scored rows, min 4 / max 12);
# data/pron/v2/scoring_config.json records the exact config used.

# build the balanced v2 shortlist (density-within-type, excludes 06EA–06EC)
python scripts/pron_shortlist_v2.py \
    --scores data/pron/v2/ayah_scores.csv \
    --out data/pron/pron_shortlist_v2.csv

# dual-script join -> data/pron_shortlist_dualscript.jsonl
python scripts/pron_dualscript.py

# write the 80 caption files -> data/pron/captions/
python scripts/pron_captions.py

# audit donor audio coverage (LISTING ONLY; ~30s, hits GCS)
python scripts/audio_audit.py --shortlist data/pron/pron_shortlist_v2.csv \
    --out data/pron/reciter_audit.json

# probe the 6 scratch mp3s -> data/pron/ffprobe_scratch.json
python scripts/pron_ffprobe_scratch.py --out data/pron/ffprobe_scratch.json \
    --scratch /tmp/opencode/audio_audit
```

## Key files

Scoring / text:
- `aya_scoring/arabic.py` — `strip_pause_marks()`, `is_pause_sign()`, and the codepoint sets (`PAUSE_SIGN_CODEPOINTS` 06D6–06DC, `NON_PRONUNCIATION_CODEPOINTS` {06DD,06DE,06E9}, `PROTECTED_PRONUNCIATION_CODEPOINTS` 06DF–06E8/06ED/0670/064B–065F, `UNDECIDED_HIGH_STOP_CODEPOINTS` {06EA,06EB,06EC}).
- `configs/pron_hkhad.json` — letter weights `ح=5 خ=5 ع=5 ض=6`, rest default.
- `data/pron/v2/ayah_scores.csv` — all scored ayat (min 4 / max 12 words).
- `data/pron/v2/dropped_ayahs.csv`, `data/pron/v2/scoring_config.json` — provenance.
- `data/pron/pron_shortlist_v2.csv` — **the 40-row shortlist** (`key, surah, ayah, word_count, length_type, raw_score, density_score, count_ح/خ/ع/ض`).
- `data/pron_shortlist_dualscript.jsonl` — 40 records joining uthmani+simple (`*_raw`, `*_clean`, `word_count_*`, `length_type`).
- `data/pron/captions/{key}_{uthmani,simple}.txt` — 80 caption files.
- `data/quran/manifest.json` — corpus provenance (both editions 6236 ayat).

Scripts:
- `scripts/pron_shortlist_v2.py` — v2 selection. `EXCLUDE_CODEPOINTS={0x06EA,0x06EB,0x06EC}`; `TYPE_A=(4,6)`, `TYPE_B=(7,12)`; `balanced_pick()` per-letter fallback.
- `scripts/pron_dualscript.py` — defaults to `data/pron/pron_shortlist_v2.csv`; adds `length_type`.
- `scripts/pron_captions.py` — writes the caption files.
- `scripts/audio_audit.py` — classifies reciter dirs + GCS listing-only coverage.
- `scripts/pron_ffprobe_scratch.py` — downloads-if-missing + ffprobe, commits numbers only.
- Tests: `tests/test_pause_marks.py`, `tests/test_captions.py` (plus pre-existing `tests/test_pipeline.py`).

Audio audit outputs:
- `data/pron/reciter_audit.json` — per-dir classification + coverage; includes a `style_metadata_note` key.
- `data/pron/ffprobe_scratch.json` — the 6 ffprobe results.

## Decisions and results to remember

Shortlist (Task 2):
- 40 ayat: **25 type A (4–6 words), 15 type B (7–12 words)**, ranked by density within type.
- Word-count dist `{4:13, 5:8, 6:4, 7:5, 8:2, 9:5, 11:2, 12:1}` (no 10-word ayat selected).
- Per-letter coverage `ح 16 / خ 16 / ع 34 / ض 16` (all ≥12, so no quota fallback needed).
- Hard excludes: U+06EA/06EB/06EC ayat `011041`, `012011` (caught in-range by the codepoint filter); `041044` is longer than 12 words so it never qualifies. All three are absent from the shortlist and the JSONL. Verified no 06EA–EC in any JSONL raw text.
- Dual-script join: 40/40 matched, **0 word-count differences**, unmatched none.

Caption layout (exact, single trailing newline; no trigger word, no `""...""`, no melisma/Intro/Outro):
```
Solo male voice, unaccompanied. Clear precise Arabic diction, measured pace.
[Lyrics]
[Verse]
<*_clean text>
```

Donor audio bucket:
- `gs://sheikh-fitzgerald-backup/ARABIC_DATA/QURAN_VERSE_BY_VERSE_RECITATIONS_DATASETS/` with per-verse `SSSAAA.mp3` (key = zero-padded surah+ayah, e.g. `023041`).
- `gcloud storage ls` works from this environment.
- Metadata: `_catalog.json`, `_registry/registry.json` (+ `_registry/registry_history/*`), `_metadata/<dir>/reciter.json` / `_COMPLETE.json` / `zip_manifest.json` / `checksum.md5` / license.
- **No style/type/category field anywhere** in those three sources; every `note` field is empty. The only style signal is the directory/name string (documented in `reciter_audit.json` → `style_metadata_note`).
- Excluded dirs: `warsh` (Warsh riwayah), `Abdul_Basit_Mujawwad_128kbps`, `Husary_128kbps_Mujawwad`.
- **Every non-excluded reciter dir has full 40/40 coverage** of the shortlist.
- Classification: name-keyword match first, else curated 26-name common-knowledge Murattal fallback; `classification_source` is `name-keyword` / `common-knowledge` / `none`. Result: 28 Murattal (2 keyword + 26 common-knowledge), 1 Muallim (`Husary_Muallim_128kbps`), 2 Mujawwad excluded, `warsh` stays `unknown` (excluded).
- Duplicate: `Ahmed_ibn_Ali_al-Ajamy_128kbps_ketaballah.net` and `ahmed_ibn_ali_al_ajamy_128kbps` are the same reciter in two encodings (registry ids 13 & 71; differing source URLs, zip sha256, bytes). Counted **once**; `duplicate_of` is recorded in the audit.

Part C step 8 — the 6 probed files (all mp3, 44100 Hz, 2 ch, 128 kbps):
```
Husary_Muallim_128kbps__068030.mp3    dur=12.826125s  (type A)
Husary_Muallim_128kbps__026148.mp3    dur=10.710188s  (type A)
Husary_Muallim_128kbps__023041.mp3    dur=24.189375s  (type B)
Minshawy_Murattal_128kbps__068030.mp3 dur=7.217750s   (type A)
Minshawy_Murattal_128kbps__026148.mp3 dur=6.042313s   (type A)
Minshawy_Murattal_128kbps__023041.mp3 dur=14.349188s  (type B)
```

## Gotchas

- **Write results to files, not just chat.** Reports pasted in replies were lost across sessions; that is why the ffprobe numbers now live in `data/pron/ffprobe_scratch.json`.
- Git identity is not configured here; commit with `git -c user.name=... -c user.email=...`.
- Scratch downloads go to `/tmp/opencode/audio_audit/` and must **not** be committed (do not commit audio). Only listing/probe numbers are committed.
- `output/` and `.pytest_cache/` are gitignored. v2 scorer output is committed under `data/pron/v2/` on purpose.
- Do not commit secrets. No merges, no training, no model repos unless a new task says so.

## Open items / likely next steps

- No blocking work items; the three tasks asked so far are complete.
- Not yet done (only if a future task asks): pick the final donor reciter and
  actually fetch audio for the 40 ayat; align audio to the cleaned text; build
  the LoRA training dataset; any model work.
