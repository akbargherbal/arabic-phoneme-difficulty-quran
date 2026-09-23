#!/usr/bin/env python3
"""Download the full 350-ayah donor set for every shortlisted reciter.

Reads the shortlist keys and the reciter directories from
``reciters_shortlist/shortlist.txt`` (on ``main`` if the file is not present on
the current branch), then mirrors ``<bucket>/<reciter>/<SSSAAA>.mp3`` into
``<dest>/<reciter>/<SSSAAA>.mp3``.  The audio is intentionally NOT committed:
``data/pron/donor_audio/`` is gitignored and is the path the training run reads
from.

Idempotent: re-running skips any reciter whose local ``.mp3`` count already
matches the shortlist size, and uses ``gcloud storage cp -n`` so existing files
are never re-fetched.

Usage:
    python scripts/pron_download_donors.py \
        --shortlist data/pron/pron_shortlist_v2.csv \
        --dest data/pron/donor_audio
"""

from __future__ import annotations

import argparse
import csv
import os
import subprocess
import sys

BUCKET = (
    "gs://sheikh-fitzgerald-backup/ARABIC_DATA/"
    "QURAN_VERSE_BY_VERSE_RECITATIONS_DATASETS"
)
DEFAULT_RECITERS_FILE = "reciters_shortlist/shortlist.txt"
DEFAULT_RECITERS_REF = "main"


def load_keys(shortlist: str) -> list[str]:
    with open(shortlist, encoding="utf-8", newline="") as fh:
        return [row["key"] for row in csv.DictReader(fh)]


def read_reciters(reciters_file: str, git_ref: str) -> list[str]:
    """Read reciter dir basenames from the file, or from ``git_ref`` if absent."""
    text = None
    if os.path.exists(reciters_file):
        with open(reciters_file, encoding="utf-8") as fh:
            text = fh.read()
    else:
        out = subprocess.run(
            ["git", "show", f"{git_ref}:{reciters_file}"],
            capture_output=True, text=True, check=True,
        )
        text = out.stdout
    return [
        os.path.basename(line.strip().rstrip("/"))
        for line in text.splitlines()
        if line.strip()
    ]


def local_count(dest_dir: str) -> int:
    if not os.path.isdir(dest_dir):
        return 0
    return sum(1 for n in os.listdir(dest_dir) if n.endswith(".mp3"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--shortlist", default="data/pron/pron_shortlist_v2.csv")
    ap.add_argument("--dest", default="data/pron/donor_audio")
    ap.add_argument("--reciters-file", default=DEFAULT_RECITERS_FILE)
    ap.add_argument("--reciters-ref", default=DEFAULT_RECITERS_REF)
    args = ap.parse_args(argv)

    keys = load_keys(args.shortlist)
    reciters = read_reciters(args.reciters_file, args.reciters_ref)
    expected = len(keys) * len(reciters)
    print(f"shortlist keys : {len(keys)}")
    print(f"reciters       : {len(reciters)} -> {', '.join(reciters)}")
    print(f"expected files : {expected}")
    print(f"dest           : {os.path.abspath(args.dest)}")

    for reciter in reciters:
        dest_dir = os.path.join(args.dest, reciter)
        have = local_count(dest_dir)
        if have == len(keys):
            print(f"  [skip] {reciter:<32} {have}/{len(keys)} already present")
            continue
        os.makedirs(dest_dir, exist_ok=True)
        sources = [f"{BUCKET}/{reciter}/{key}.mp3" for key in keys]
        # Batch by 100 sources to keep each gcloud invocation small/robust.
        for i in range(0, len(sources), 100):
            proc = subprocess.run(
                ["gcloud", "storage", "cp", "-n", *sources[i:i + 100], dest_dir],
                capture_output=True, text=True,
            )
            if proc.returncode != 0:
                print(proc.stderr, file=sys.stderr)
                raise SystemExit(proc.returncode)
        have = local_count(dest_dir)
        status = "ok" if have == len(keys) else "SHORT"
        print(f"  [{status}] {reciter:<32} {have}/{len(keys)} downloaded")

    total = sum(local_count(os.path.join(args.dest, r)) for r in reciters)
    print(f"\nfiles on disk  : {total}/{expected}")
    if total != expected:
        print("ERROR: file count mismatch", file=sys.stderr)
        return 1
    print("count verified : OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
