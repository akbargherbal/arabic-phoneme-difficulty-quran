# Pronunciation-LoRA prep — progress handoff

Read this first when resuming work on branch `pron-lora-prep`. It records what
was done, where everything lives, and the gotchas that repeatedly bit us.
Last updated: 2026-09-24 (session 11).

Scope so far: **no training, no model repos, no merges.** Work has been (a) a
weighted text scorer, (b) a **350-ayah** pronunciation shortlist + caption files
split across uthmani/simple script, (c) an audit of the donor recitation audio
bucket in GCS, and (d) the full 9-reciter x 350-ayah donor **audio download +
ffprobe manifest**. Nothing here trains a model yet.

## Branch / commits

Branch: `pron-lora-prep` (pushed to `origin`). Commits, newest last:

| commit | summary |
|---|---|
| `aebaf26` | weighted scorer, pause-mark helper, dual-script join (Task 1) |
| `e779626` | re-cut shortlist to 4–12 words with A/B types; captions; donor-audio audit (Task 2) |
| `816628b` | reciter style classified via common-knowledge fallback + `classification_source` (Task 3, gap 1) |
| `852c6fe` | committed ffprobe results for the Part C scratch download (Task 3, gap 2) |
| `369a299` | duration probe across all 9 reciters (Task 4) |
| `8cac7a6` | Task 5a: rebuild shortlist/captions at 350 scale + re-audit coverage |
| (Task 5b) | Task 5b: download 9x350 donor audio + commit ffprobe manifest |

PR link (branch): https://github.com/akbargherbal/arabic-phoneme-difficulty-quran/pull/new/pron-lora-prep

## Reproduce / re-run

```bash
# full test suite (422 tests currently pass)
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
    --out data/pron/pron_shortlist_v2.csv \
    --type-a-count 219 --type-b-count 131 --min-letter-coverage 12

# dual-script join -> data/pron_shortlist_dualscript.jsonl
python scripts/pron_dualscript.py

# write the 700 caption files -> data/pron/captions/
python scripts/pron_captions.py

# audit donor audio coverage (LISTING ONLY; ~30s, hits GCS)
python scripts/audio_audit.py --shortlist data/pron/pron_shortlist_v2.csv \
    --out data/pron/reciter_audit.json

# download the 9x350 donor mp3s -> data/pron/donor_audio/ (gitignored)
python scripts/pron_download_donors.py \
    --shortlist data/pron/pron_shortlist_v2.csv \
    --dest data/pron/donor_audio

# ffprobe all 3150 files -> data/pron/donor_audio_manifest.json
python scripts/pron_donor_manifest.py \
    --audio-dir data/pron/donor_audio \
    --shortlist data/pron/pron_shortlist_v2.csv \
    --out data/pron/donor_audio_manifest.json

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
- `data/pron/pron_shortlist_v2.csv` — **the 350-row shortlist** (`key, surah, ayah, word_count, length_type, raw_score, density_score, count_ح/خ/ع/ض`).
- `data/pron_shortlist_dualscript.jsonl` — 350 records joining uthmani+simple (`*_raw`, `*_clean`, `word_count_*`, `length_type`).
- `data/pron/captions/{key}_{uthmani,simple}.txt` — 700 caption files.
- `data/pron/donor_audio/<reciter>/<key>.mp3` — **the donor corpus the training run reads** (3150 mp3s, gitignored, not committed).
- `data/quran/manifest.json` — corpus provenance (both editions 6236 ayat).

Scripts:
- `scripts/pron_shortlist_v2.py` — v2 selection. `EXCLUDE_CODEPOINTS={0x06EA,0x06EB,0x06EC}`; `TYPE_A=(4,6)`, `TYPE_B=(7,12)`; `balanced_pick()` per-letter fallback.
- `scripts/pron_dualscript.py` — defaults to `data/pron/pron_shortlist_v2.csv`; adds `length_type`.
- `scripts/pron_captions.py` — writes the caption files.
- `scripts/audio_audit.py` — classifies reciter dirs + GCS listing-only coverage.
- `scripts/pron_download_donors.py` — mirrors the 9x350 donor mp3s into `data/pron/donor_audio/` (idempotent, `gcloud storage cp -n`).
- `scripts/pron_donor_manifest.py` — ffprobe every donor file, writes the committed summary manifest.
- `scripts/pron_ffprobe_scratch.py` — downloads-if-missing + ffprobe, commits numbers only.
- Tests: `tests/test_pause_marks.py`, `tests/test_captions.py` (plus pre-existing `tests/test_pipeline.py`).

Audio audit outputs:
- `data/pron/reciter_audit.json` — per-dir classification + coverage; includes a `style_metadata_note` key.
- `data/pron/donor_audio_manifest.json` — per-reciter count/duration/format + failures/anomalies for all 3150 donors.
- `data/pron/ffprobe_scratch.json` — the 6 ffprobe results.

## Decisions and results to remember

Shortlist (Task 5, superseding the 40-ayah Task 2 cut):
- 350 ayat: **219 type A (4–6 words), 131 type B (7–12 words)**, ranked by density within type.
- Method `density-within-type` — the `--min-letter-coverage 12` floor was already met, so no `balanced-per-letter` fallback.
- Word-count dist `{4:95, 5:73, 6:51, 7:32, 8:37, 9:26, 10:12, 11:13, 12:11}`.
- Per-letter coverage `ح 166 / خ 113 / ع 277 / ض 86` (all ≥12).
- Hard excludes: U+06EA/06EB/06EC ayat `011041`, `012011` (unchanged from Task 2).
- Dual-script join: 350/350 matched, **3 word-count differences** (`020083` u5/s6, `072016` u7/s8, `012039` u9/s10), unmatched none.
- Captions: 700 files (350 x uthmani/simple). Tests: 422 passed.

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
- **Every non-excluded reciter dir has full 350/350 coverage** of the 350-ayah shortlist (re-checked, listing-only).
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

## Donor corpus (Task 5)

The training run reads donors from **`data/pron/donor_audio/<reciter>/<SSSAAA>.mp3`**
(absolute: `/content/arabic-phoneme-difficulty-quran/data/pron/donor_audio/`).
It is gitignored (`.gitignore` → `data/pron/donor_audio/`), so the 3150 mp3s
never enter git; only the summary manifest is committed.

- Downloaded 3150/3150 (9 reciters x 350), 0 missing, 0 failures (no
  non-positive duration / corrupt / unreadable files).
- Total duration 29904.819551 s (~8h 18m); mean 9.493594 s, min 3.038250 s,
  max 39.923125 s.
- All files are mp3. Baseline format 44100 Hz / 2 ch, 128/192 kbps per dir name.

Per-reciter total duration (s): Husary 4751.156, Abdul_Basit 3316.996,
Abu_Bakr 2803.820, Minshawy 3107.610, Hudhaify 3269.745, Muhammad_Ayyoub
3546.386, Yaser_Salamah 3050.761, aziz_alili 2845.976, Abdullah_Basfar 3213.369.

Anomalies (flagged, not averaged away; exact keys in
`data/pron/donor_audio_manifest.json` → `unexpected_format_files`):
- `Abu_Bakr_Ash-Shaatree_128kbps`: 14/350 at 11025 Hz / mono / 24 kbps.
- `Hudhaify_128kbps`: 16/350 at 192 kbps (dir says 128).
- `Muhammad_Ayyoub_128kbps`: 1/350 (`007199`) at 192 kbps.
- `Yaser_Salamah_128kbps`: 5/350 at 48000 Hz.
- `Abdullah_Basfar_192kbps`: 1/350 (`029054`) at 128 kbps (dir says 192).

## Gotchas

- **Write results to files, not just chat.** Reports pasted in replies were lost across sessions; that is why the ffprobe numbers now live in `data/pron/ffprobe_scratch.json`.
- Git identity is not configured here; commit with `git -c user.name=... -c user.email=...`.
- Scratch probe downloads go to `/tmp/opencode/...` and must **not** be committed. The real donor corpus lives at the gitignored `data/pron/donor_audio/`; only probe/manifest numbers are committed.
- `output/`, `.pytest_cache/` and `data/pron/donor_audio/` are gitignored. v2 scorer output is committed under `data/pron/v2/` on purpose.
- Do not commit secrets. No merges, no training, no model repos unless a new task says so.

## Open items / likely next steps

- Donor audio is downloaded at `data/pron/donor_audio/`; no manifest failures.
  The 37 format-anomaly files (see manifest) are usable but differ from their
  dir-implied encoding — worth a resample/consistency decision before training.
- Not yet done (only if a future task asks): pick the final donor reciter,
  align audio to the cleaned text, build the LoRA training dataset, any model work.

## Exclusion reversal (Task 11, 2026-09-24)

Task 7 excluded `Hudhaify_128kbps/023005` and `aziz_alili_128kbps/086008` because
libsndfile could not decode them. Task 10 showed both decode fully under
ffmpeg, torchaudio and librosa (torchaudio is the decoder documented for the
training loader); the exclusion was reversed as a false positive. Final
exclusion list is empty (`data/pron/training_pair_exclusions.json` = `[]`), i.e.
3150 files / 6300 dual-script training pairs, 0 exclusions.
