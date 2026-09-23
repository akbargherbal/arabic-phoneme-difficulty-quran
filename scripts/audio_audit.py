#!/usr/bin/env python3
"""Audit donor reciter directories for per-verse coverage of the shortlist.

LISTING ONLY: this reads the bucket's top-level reciter prefixes and lists each
directory's objects to see which of the shortlisted ``SSSAAA.mp3`` files exist.
No audio is downloaded here (a separate scratch step probes a few files).

Classification is by directory name (the catalog/registry/metadata carry
riwayah, verse counts and provenance but no performance-style field).  ``warsh``
and the two Mujawwad directories are excluded per policy.

Usage:
    python scripts/audio_audit.py \
        --shortlist data/pron/pron_shortlist_v2.csv \
        --out data/pron/reciter_audit.json
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

BUCKET = (
    "gs://sheikh-fitzgerald-backup/ARABIC_DATA/"
    "QURAN_VERSE_BY_VERSE_RECITATIONS_DATASETS"
)
EXCLUDED_DIRS = {
    "warsh",  # Warsh riwaya: different text/numbering from Hafs Tanzil
    "Abdul_Basit_Mujawwad_128kbps",
    "Husary_128kbps_Mujawwad",
}
# Same reciter, two sources/encodings; counted once (see registry ids 13 & 71).
DUPLICATE_OF = {
    "Ahmed_ibn_Ali_al-Ajamy_128kbps_ketaballah.net": "ahmed_ibn_ali_al_ajamy_128kbps",
}


def classify(dirname: str) -> str:
    name = dirname.lower()
    if "mujawwad" in name:
        return "Mujawwad"
    if "muallim" in name:
        return "Muallim"
    if "murattal" in name:
        return "Murattal"
    return "unknown"


def gcloud_ls(prefix: str) -> list[str]:
    out = subprocess.run(
        ["gcloud", "storage", "ls", prefix],
        capture_output=True, text=True, check=True,
    ).stdout
    return [line.strip() for line in out.splitlines() if line.strip()]


def list_reciter_dirs() -> list[str]:
    prefix = BUCKET.rstrip("/")
    dirs = set()
    for line in gcloud_ls(BUCKET + "/"):
        if not line.endswith("/"):
            continue
        stripped = line.rstrip("/")
        if stripped == prefix:  # the listing echoes the bucket itself
            continue
        name = stripped.rsplit("/", 1)[-1]
        if name and not name.startswith("_"):
            dirs.add(name)
    return sorted(dirs)


def coverage_for_dir(dirname: str, keys: set[str]) -> dict:
    wanted = {key for key in keys}
    present: set[str] = set()
    for line in gcloud_ls(f"{BUCKET}/{dirname}/"):
        base = line.rsplit("/", 1)[-1]
        if base.endswith(".mp3"):
            key = base[:-4]
            if key in wanted:
                present.add(key)
    return {
        "dir": dirname,
        "count": len(present),
        "missing": sorted(wanted - present),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--shortlist", default="data/pron/pron_shortlist_v2.csv")
    ap.add_argument("--out", default="data/pron/reciter_audit.json")
    args = ap.parse_args(argv)

    with open(args.shortlist, encoding="utf-8", newline="") as fh:
        keys = {row["key"] for row in csv.DictReader(fh)}

    dirs = list_reciter_dirs()
    to_scan = [d for d in dirs if d not in EXCLUDED_DIRS]

    results: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        for res in pool.map(lambda d: coverage_for_dir(d, keys), to_scan):
            results[res["dir"]] = res

    audit = []
    for dirname in dirs:
        entry = {
            "dir": dirname,
            "classification": classify(dirname),
            "excluded": dirname in EXCLUDED_DIRS,
            "duplicate_of": DUPLICATE_OF.get(dirname),
        }
        if dirname in results:
            entry["coverage"] = results[dirname]["count"]
            entry["total"] = len(keys)
            entry["full_coverage"] = results[dirname]["count"] == len(keys)
            entry["missing"] = results[dirname]["missing"]
        audit.append(entry)

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(
            {"bucket": BUCKET, "shortlist_count": len(keys), "reciters": audit},
            fh, ensure_ascii=False, indent=2,
        )

    header = f"{'directory':<48} {'class':<9} {'cov':>5} {'dup':<30} excl"
    print(header)
    print("-" * len(header))
    for e in sorted(
        audit,
        key=lambda x: (x["excluded"], -x.get("coverage", -1)),
    ):
        dup = e["duplicate_of"] or ""
        print(
            f"{e['dir']:<48} {e['classification']:<9} "
            f"{e.get('coverage', '-'):>5} {dup:<30} {'yes' if e['excluded'] else ''}"
        )
    print(f"\nwritten: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
