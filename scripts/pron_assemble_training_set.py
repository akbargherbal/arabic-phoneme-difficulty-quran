#!/usr/bin/env python3
"""Assemble the flat pronunciation training set that ai-toolkit reads.

Inputs (read-only)::

    data/pron/pron_shortlist_v2.csv          # 350 keys x {A,B} + letter counts
    data/pron/donor_audio/<reciter>/<key>.mp3 # 9 reciters x 350 keys
    data/pron/captions/<key>_<script>.txt     # 700 captions
    data/pron/training_pair_exclusions.json   # 10 excluded (reciter, key)
    data/pron/donor_audio_manifest.json       # reciter list + progress
    data/pron/audio_level_silence_report.json # per-file duration_s (no ffprobe)

Outputs (``data/pron/training_set/`` is gitignored)::

    data/pron/training_set/{train,val,smoke}/<stem>.mp3
    data/pron/training_set/{train,val,smoke}/<stem>.txt
    data/pron/training_set_manifest.json      # committed, per pair
    data/pron/holdout_manifest.json           # committed, ayah-level hold-out
    data/pron/training_set_report.json        # committed, verification numbers

Every mp3 is a byte-for-byte copy of its donor source and every txt a
byte-for-byte copy of its caption (no re-encode/resample/trim/normalize and no
symlinks).  The same audio therefore appears twice, under the ``uthmani`` and
``simple`` stems.  An excluded ``(reciter, key)`` drops BOTH scripts.

Usage::

    python scripts/pron_assemble_training_set.py [--force]

Re-running into a non-empty output directory refuses without ``--force``; with
``--force`` the directory is rebuilt from scratch and is byte-identical.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import random
import shutil
import sys
from collections import Counter

SCRIPTS = ("uthmani", "simple")
SEED = 42
HOLDOUT_TYPE_A = 6
HOLDOUT_TYPE_B = 4
HOLDOUT_TOTAL = HOLDOUT_TYPE_A + HOLDOUT_TYPE_B
REQUIRED_LETTERS = ("ح", "خ", "ع", "ض")
SMOKE_AYAT = 4
SMOKE_RECITERS = 2
FORBIDDEN_SUBSTRINGS = ("arabmaqamrock", "...", "…")
SPLIT_ORDER = ("train", "val", "smoke")

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHORTLIST = os.path.join(REPO_ROOT, "data", "pron", "pron_shortlist_v2.csv")
AUDIO_ROOT = os.path.join(REPO_ROOT, "data", "pron", "donor_audio")
CAPTIONS_DIR = os.path.join(REPO_ROOT, "data", "pron", "captions")
EXCLUSIONS = os.path.join(REPO_ROOT, "data", "pron", "training_pair_exclusions.json")
DONOR_MANIFEST = os.path.join(REPO_ROOT, "data", "pron", "donor_audio_manifest.json")
DURATION_REPORT = os.path.join(REPO_ROOT, "data", "pron", "audio_level_silence_report.json")
INTEGRITY = os.path.join(REPO_ROOT, "data", "pron", "integrity_check.json")
OUT_ROOT = os.path.join(REPO_ROOT, "data", "pron", "training_set")
MANIFEST_OUT = os.path.join(REPO_ROOT, "data", "pron", "training_set_manifest.json")
HOLDOUT_OUT = os.path.join(REPO_ROOT, "data", "pron", "holdout_manifest.json")
REPORT_OUT = os.path.join(REPO_ROOT, "data", "pron", "training_set_report.json")


# --------------------------------------------------------------------------- #
# Pure helpers (unit-tested)
# --------------------------------------------------------------------------- #
def md5_file(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def make_stem(reciter: str, key: str, script: str) -> str:
    return f"{reciter}_{key}_{script}"


def parse_stem(stem: str):
    """Parse ``<reciter>_<key>_<script>`` -> (reciter, key, script)."""
    head, script = stem.rsplit("_", 1)
    reciter, key = head.rsplit("_", 1)
    if script not in SCRIPTS:
        raise ValueError(f"unknown script in stem: {stem!r}")
    if len(key) != 6 or not key.isdigit():
        raise ValueError(f"bad key in stem: {stem!r}")
    return reciter, key, script


def read_shortlist(path: str):
    with open(path, encoding="utf-8") as fh:
        rows = []
        for raw in csv.DictReader(fh):
            row = dict(raw)
            row["length_type"] = row["length_type"].strip()
            for letter in REQUIRED_LETTERS:
                row[f"count_{letter}"] = int(row[f"count_{letter}"])
            rows.append(row)
    return rows


def load_exclusions(path: str):
    """Return set of excluded (reciter, key) pairs."""
    with open(path, encoding="utf-8") as fh:
        return {(e["reciter"], e["key"]) for e in json.load(fh)}


def simple_text_groups(captions_dir: str, keys):
    """Map key -> simple caption bytes; return (map, duplicate groups)."""
    texts = {}
    for key in keys:
        with open(os.path.join(captions_dir, f"{key}_simple.txt"), "rb") as fh:
            texts[key] = fh.read()
    groups = {}
    for key, data in texts.items():
        groups.setdefault(data, []).append(key)
    duplicates = sorted(
        (sorted(v) for v in groups.values() if len(v) > 1)
    )
    return texts, duplicates


def unique_simple_keys(captions_dir: str, keys):
    """Keys whose simple caption is unique among ``keys`` (plus duplicates)."""
    _, duplicates = simple_text_groups(captions_dir, keys)
    dup = {k for group in duplicates for k in group}
    return {k for k in keys if k not in dup}, duplicates


def covers_letters(rows, letters=REQUIRED_LETTERS) -> bool:
    for letter in letters:
        if not any(row[f"count_{letter}"] > 0 for row in rows):
            return False
    return True


def select_holdout(a_pool, b_pool, seed: int = SEED,
                   n_a: int = HOLDOUT_TYPE_A, n_b: int = HOLDOUT_TYPE_B,
                   letters=REQUIRED_LETTERS):
    """Deterministic seeded draw of ``n_a`` type-A + ``n_b`` type-B ayat that
    together cover ``letters``.  Returns (chosen rows sorted by key, attempt)."""
    a_pool = sorted(a_pool, key=lambda r: r["key"])
    b_pool = sorted(b_pool, key=lambda r: r["key"])
    if len(a_pool) < n_a or len(b_pool) < n_b:
        raise ValueError("not enough candidates for the requested hold-out")
    rng = random.Random(seed)
    for attempt in range(1, 100001):
        chosen = rng.sample(a_pool, n_a) + rng.sample(b_pool, n_b)
        if covers_letters(chosen, letters):
            return sorted(chosen, key=lambda r: r["key"]), attempt
    raise RuntimeError("could not cover all letters within 100000 attempts")


def build_plan(rows, reciters, exclusions, holdout_keys, smoke_keys,
               smoke_reciters):
    """Return the list of pair dicts to write (split, reciter, key, script)."""
    holdout_keys = set(holdout_keys)
    smoke_keys = set(smoke_keys)
    pairs = []
    for row in rows:
        key = row["key"]
        split = "val" if key in holdout_keys else "train"
        for reciter in reciters:
            if (reciter, key) in exclusions:
                continue  # drops both scripts
            for script in SCRIPTS:
                pairs.append({"split": split, "reciter": reciter,
                              "key": key, "script": script})
    for key in sorted(smoke_keys):
        for reciter in smoke_reciters:
            if (reciter, key) in exclusions or key in holdout_keys:
                raise ValueError(f"smoke pair not in train: {reciter}/{key}")
            for script in SCRIPTS:
                pairs.append({"split": "smoke", "reciter": reciter,
                              "key": key, "script": script})
    return pairs


def find_forbidden(text: str):
    return [sub for sub in FORBIDDEN_SUBSTRINGS if sub in text]


# --------------------------------------------------------------------------- #
# Build + verify
# --------------------------------------------------------------------------- #
def _dir_is_nonempty(path: str) -> bool:
    return os.path.isdir(path) and bool(os.listdir(path))


def copy_pairs(plan, out_root, audio_root, captions_dir):
    for split in SPLIT_ORDER:
        os.makedirs(os.path.join(out_root, split), exist_ok=True)
    for pair in plan:
        split, reciter, key, script = (pair["split"], pair["reciter"],
                                       pair["key"], pair["script"])
        stem = make_stem(reciter, key, script)
        src_audio = os.path.join(audio_root, reciter, f"{key}.mp3")
        src_txt = os.path.join(captions_dir, f"{key}_{script}.txt")
        shutil.copyfile(src_audio, os.path.join(out_root, split, f"{stem}.mp3"))
        shutil.copyfile(src_txt, os.path.join(out_root, split, f"{stem}.txt"))


def scan_dir(path):
    names = os.listdir(path)
    mp3 = {n[:-4] for n in names if n.endswith(".mp3")}
    txt = {n[:-4] for n in names if n.endswith(".txt")}
    symlinks = sorted(n for n in names if os.path.islink(os.path.join(path, n)))
    return mp3, txt, symlinks


def verify(out_root, plan, exclusions, holdout_keys, donor_md5, duration_map):
    """Re-read the output folders and compute every verification number."""
    exclusions = set(exclusions)
    holdout_keys = set(holdout_keys)

    by_split = {}
    mp3_stems, txt_stems, symlinks = {}, {}, {}
    for split in SPLIT_ORDER:
        d = os.path.join(out_root, split)
        m, t, s = scan_dir(d)
        mp3_stems[split], txt_stems[split], symlinks[split] = m, t, s
        by_split[split] = {"mp3_stems": m, "txt_stems": t, "dir": d}

    counts = {split: len(mp3_stems[split]) for split in SPLIT_ORDER}
    per_reciter = {split: Counter() for split in SPLIT_ORDER}
    per_script = {split: Counter() for split in SPLIT_ORDER}
    for split in SPLIT_ORDER:
        for stem in mp3_stems[split]:
            reciter, _key, script = parse_stem(stem)
            per_reciter[split][reciter] += 1
            per_script[split][script] += 1

    # 1:1 stems / orphans
    stem_report = {}
    for split in SPLIT_ORDER:
        orphans_mp3 = sorted(mp3_stems[split] - txt_stems[split])
        orphans_txt = sorted(txt_stems[split] - mp3_stems[split])
        stem_report[split] = {
            "mp3_count": len(mp3_stems[split]),
            "txt_count": len(txt_stems[split]),
            "one_to_one": not orphans_mp3 and not orphans_txt,
            "mp3_without_txt": orphans_mp3,
            "txt_without_mp3": orphans_txt,
            "symlink_count": len(symlinks[split]),
            "symlinks": symlinks[split],
        }

    # md5 of every output mp3 vs donor source
    md5_mismatches = []
    md5_checked = 0
    out_md5 = {}
    for split in SPLIT_ORDER:
        d = os.path.join(out_root, split)
        for stem in sorted(mp3_stems[split]):
            reciter, key, script = parse_stem(stem)
            digest = md5_file(os.path.join(d, f"{stem}.mp3"))
            out_md5[(split, stem)] = digest
            md5_checked += 1
            if digest != donor_md5[(reciter, key)]:
                md5_mismatches.append({"split": split, "stem": stem})

    # txt byte-identity vs source caption
    txt_mismatches = []
    forbidden_hits = []
    for split in SPLIT_ORDER:
        d = os.path.join(out_root, split)
        for stem in sorted(txt_stems[split]):
            reciter, key, script = parse_stem(stem)
            with open(os.path.join(d, f"{stem}.txt"), "rb") as fh:
                got = fh.read()
            with open(os.path.join(CAPTIONS_DIR, f"{key}_{script}.txt"), "rb") as fh:
                want = fh.read()
            if got != want:
                txt_mismatches.append({"split": split, "stem": stem})
            hits = find_forbidden(got.decode("utf-8", errors="replace"))
            if hits:
                forbidden_hits.append({"split": split, "stem": stem, "hits": hits})

    # excluded pairs absent everywhere (both scripts)
    all_stems = set()
    for split in SPLIT_ORDER:
        all_stems |= mp3_stems[split] | txt_stems[split]
    excluded_present = []
    for reciter, key in sorted(exclusions):
        for script in SCRIPTS:
            stem = make_stem(reciter, key, script)
            if stem in all_stems:
                excluded_present.append({"reciter": reciter, "key": key,
                                         "script": script})

    # no held-out key in train
    train_reciters = sorted({parse_stem(s)[0] for s in mp3_stems["train"]})
    holdout_in_train = []
    for key in sorted(holdout_keys):
        for reciter in train_reciters:
            for script in SCRIPTS:
                if make_stem(reciter, key, script) in mp3_stems["train"]:
                    holdout_in_train.append({"reciter": reciter, "key": key,
                                             "script": script})

    # smoke subset of train
    smoke_not_in_train = sorted(s for s in mp3_stems["smoke"]
                                if s not in mp3_stems["train"])

    # durations from the existing per-file report (no new ffprobe)
    duration = {}
    for split in SPLIT_ORDER:
        total = 0.0
        unique = {}
        for stem in mp3_stems[split]:
            reciter, key, _script = parse_stem(stem)
            total += duration_map[(reciter, key)]
            unique[(reciter, key)] = duration_map[(reciter, key)]
        duration[split] = {
            "pair_duration_s": round(total, 6),
            "unique_audio_duration_s": round(sum(unique.values()), 6),
            "unique_audio_files": len(unique),
        }

    disk_bytes = {}
    for split in SPLIT_ORDER:
        d = os.path.join(out_root, split)
        disk_bytes[split] = sum(
            os.path.getsize(os.path.join(d, n)) for n in os.listdir(d)
        )
    disk_bytes["total"] = sum(disk_bytes[s] for s in SPLIT_ORDER)

    report = {
        "counts": counts,
        "counts_per_reciter": {s: dict(sorted(per_reciter[s].items()))
                               for s in SPLIT_ORDER},
        "counts_per_script": {s: dict(sorted(per_script[s].items()))
                              for s in SPLIT_ORDER},
        "stems": stem_report,
        "md5": {"checked": md5_checked, "mismatch_count": len(md5_mismatches),
                "mismatches": md5_mismatches},
        "txt_byte_identical": {"checked": sum(len(txt_stems[s]) for s in SPLIT_ORDER),
                               "mismatch_count": len(txt_mismatches),
                               "mismatches": txt_mismatches},
        "forbidden_substrings": {"hits": forbidden_hits},
        "excluded_pairs_present": excluded_present,
        "holdout_keys_in_train": holdout_in_train,
        "smoke_not_in_train": smoke_not_in_train,
        "duration": duration,
        "disk_bytes": disk_bytes,
    }
    report["all_checks_pass"] = (
        not md5_mismatches
        and not txt_mismatches
        and not excluded_present
        and not holdout_in_train
        and not smoke_not_in_train
        and not forbidden_hits
        and all(stem_report[s]["one_to_one"] and stem_report[s]["symlink_count"] == 0
                for s in SPLIT_ORDER)
    )
    return report, out_md5


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--shortlist", default=SHORTLIST)
    ap.add_argument("--audio-root", default=AUDIO_ROOT)
    ap.add_argument("--captions", default=CAPTIONS_DIR)
    ap.add_argument("--exclusions", default=EXCLUSIONS)
    ap.add_argument("--donor-manifest", default=DONOR_MANIFEST)
    ap.add_argument("--duration-report", default=DURATION_REPORT)
    ap.add_argument("--integrity", default=INTEGRITY)
    ap.add_argument("--out-root", default=OUT_ROOT)
    ap.add_argument("--manifest-out", default=MANIFEST_OUT)
    ap.add_argument("--holdout-out", default=HOLDOUT_OUT)
    ap.add_argument("--report-out", default=REPORT_OUT)
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--force", action="store_true",
                    help="rebuild even if the output directory is non-empty")
    args = ap.parse_args(argv)

    if _dir_is_nonempty(args.out_root) and not args.force:
        print(f"refusing to write into non-empty {args.out_root!r} without --force",
              file=sys.stderr)
        return 2
    if args.force and os.path.isdir(args.out_root):
        shutil.rmtree(args.out_root)

    rows = read_shortlist(args.shortlist)
    keys = [r["key"] for r in rows]
    by_key = {r["key"]: r for r in rows}
    exclusions = load_exclusions(args.exclusions)
    excluded_keys = {k for _r, k in exclusions}
    with open(args.donor_manifest, encoding="utf-8") as fh:
        reciters = json.load(fh)["reciters"]

    # hold-out candidates: unique simple text and not excluded for any reciter
    unique_keys, duplicate_groups = unique_simple_keys(args.captions, keys)
    a_pool = [by_key[k] for k in keys
              if by_key[k]["length_type"] == "A" and k in unique_keys
              and k not in excluded_keys]
    b_pool = [by_key[k] for k in keys
              if by_key[k]["length_type"] == "B" and k in unique_keys
              and k not in excluded_keys]
    chosen_rows, attempt = select_holdout(a_pool, b_pool, seed=args.seed)
    holdout_keys = [r["key"] for r in chosen_rows]

    # smoke: first 4 sorted train keys x first 2 (non-excluded) reciters
    train_keys = sorted(k for k in keys if k not in set(holdout_keys))
    smoke_keys = train_keys[:SMOKE_AYAT]
    smoke_reciters = [r for r in reciters if not any(
        (r, k) in exclusions for k in smoke_keys)][:SMOKE_RECITERS]
    if len(smoke_reciters) < SMOKE_RECITERS:
        raise RuntimeError("not enough clean reciters for smoke")

    plan = build_plan(rows, reciters, exclusions, holdout_keys, smoke_keys,
                      smoke_reciters)
    copy_pairs(plan, args.out_root, args.audio_root, args.captions)

    # donor md5 + duration from existing committed metadata
    donor_md5 = {}
    for reciter in reciters:
        for key in keys:
            p = os.path.join(args.audio_root, reciter, f"{key}.mp3")
            donor_md5[(reciter, key)] = md5_file(p)
    with open(args.integrity, encoding="utf-8") as fh:
        integ = {(f["reciter"], f["key"]): f["local_md5"]
                 for f in json.load(fh)["files"]}
    integrity_ok = all(donor_md5[k] == integ[k] for k in donor_md5)
    with open(args.duration_report, encoding="utf-8") as fh:
        duration_map = {(f["reciter"], f["key"]): f["duration_s"]
                        for f in json.load(fh)["files"]}

    report, out_md5 = verify(args.out_root, plan, exclusions, holdout_keys,
                             donor_md5, duration_map)
    report["donor_md5_matches_integrity_check"] = integrity_ok
    report["all_checks_pass"] = report["all_checks_pass"] and integrity_ok

    # committed per-pair manifest (audio MD5 read back from the output file)
    manifest_pairs = []
    for pair in plan:
        stem = make_stem(pair["reciter"], pair["key"], pair["script"])
        manifest_pairs.append({
            "split": pair["split"],
            "reciter": pair["reciter"],
            "key": pair["key"],
            "script": pair["script"],
            "stem": stem,
            "audio_md5": out_md5[(pair["split"], stem)],
            "audio_source": os.path.relpath(
                os.path.join(args.audio_root, pair["reciter"], f"{pair['key']}.mp3"),
                REPO_ROOT),
            "caption_source": os.path.relpath(
                os.path.join(args.captions, f"{pair['key']}_{pair['script']}.txt"),
                REPO_ROOT),
        })
    manifest = {
        "generated_by": "scripts/pron_assemble_training_set.py",
        "seed": args.seed,
        "pair_count": len(manifest_pairs),
        "counts_by_split": report["counts"],
        "pairs": manifest_pairs,
    }

    holdout_manifest = {
        "generated_by": "scripts/pron_assemble_training_set.py",
        "seed": args.seed,
        "level": "ayah",
        "n_ayat": len(chosen_rows),
        "type_counts": {
            "A": sum(1 for r in chosen_rows if r["length_type"] == "A"),
            "B": sum(1 for r in chosen_rows if r["length_type"] == "B"),
        },
        "required_letters": list(REQUIRED_LETTERS),
        "selection_algorithm": (
            "Filter the 350 shortlisted ayat to unique simple-script text and "
            "not in the exclusion list, split by length_type, then draw "
            f"{HOLDOUT_TYPE_A} type-A + {HOLDOUT_TYPE_B} type-B uniformly without "
            f"replacement from the key-sorted pools using random.Random({args.seed}); "
            "the first draw whose union has >=1 ayah with count>0 for each of "
            "ح خ ع ض is the hold-out."
        ),
        "attempt": attempt,
        "candidate_pool": {"type_A": len(a_pool), "type_B": len(b_pool)},
        "uniqueness_filter": {
            "all_350_simple_texts_distinct": not duplicate_groups,
            "duplicate_groups": duplicate_groups,
        },
        "exclusion_keys_avoided": sorted(excluded_keys),
        "chosen": [
            {
                "key": r["key"],
                "surah": int(r["surah"]),
                "ayah": int(r["ayah"]),
                "length_type": r["length_type"],
                "counts": {f"count_{c}": r[f"count_{c}"] for c in REQUIRED_LETTERS},
                "covers": [c for c in REQUIRED_LETTERS if r[f"count_{c}"] > 0],
            }
            for r in chosen_rows
        ],
        "coverage": {
            c: [r["key"] for r in chosen_rows if r[f"count_{c}"] > 0]
            for c in REQUIRED_LETTERS
        },
        "rationale": (
            "Ayah-level hold-out so all 9 reciters x both scripts (180 pairs) of "
            "each chosen ayah are unseen. 6 type-A (short) + 4 type-B (long) match "
            "the shortlist's two length bands, the union covers all four target "
            "letters (ح خ ع ض), no chosen key is in the exclusion list, and unique "
            "simple text avoids train/val leakage from repeated refrains."
        ),
    }

    report["inputs"] = {
        "shortlist": os.path.relpath(args.shortlist, REPO_ROOT),
        "audio_root": os.path.relpath(args.audio_root, REPO_ROOT),
        "captions": os.path.relpath(args.captions, REPO_ROOT),
        "exclusions": os.path.relpath(args.exclusions, REPO_ROOT),
        "donor_manifest": os.path.relpath(args.donor_manifest, REPO_ROOT),
        "duration_source": os.path.relpath(args.duration_report, REPO_ROOT),
        "integrity": os.path.relpath(args.integrity, REPO_ROOT),
    }
    report["holdout_keys"] = holdout_keys
    report["smoke_keys"] = smoke_keys
    report["smoke_reciters"] = smoke_reciters
    report["expected"] = {
        "total_pairs_before_exclusions": len(rows) * len(reciters) * len(SCRIPTS),
        "excluded_pairs": len(exclusions) * len(SCRIPTS),
        "train_pairs": len(rows) * len(reciters) * len(SCRIPTS)
        - len(exclusions) * len(SCRIPTS) - len(holdout_keys) * len(reciters) * len(SCRIPTS),
        "val_pairs": len(holdout_keys) * len(reciters) * len(SCRIPTS),
        "smoke_pairs": len(smoke_keys) * len(smoke_reciters) * len(SCRIPTS),
    }

    for path, doc in ((args.manifest_out, manifest),
                      (args.holdout_out, holdout_manifest),
                      (args.report_out, report)):
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, ensure_ascii=False, indent=2)
            fh.write("\n")

    print(f"hold-out ({len(holdout_keys)}): {', '.join(holdout_keys)}")
    print(f"smoke keys {smoke_keys} reciters {smoke_reciters}")
    for split in SPLIT_ORDER:
        print(f"{split:5s}: {report['counts'][split]:5d} pairs  "
              f"{report['disk_bytes'][split]} bytes  "
              f"{report['duration'][split]['pair_duration_s']} s audio")
    print(f"all checks pass: {report['all_checks_pass']}")
    print(f"wrote {args.manifest_out}, {args.holdout_out}, {args.report_out}")
    return 0 if report["all_checks_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
