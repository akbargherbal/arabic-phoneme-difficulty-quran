#!/usr/bin/env python3
"""Verify the local donor corpus is byte-identical to the GCS source.

For every ``<reciter>/<key>.mp3`` this compares the local MD5 against the
``md5Hash`` stored in the GCS object metadata (no re-download).  Any mismatch
is listed explicitly; ``all_match`` is false if anything differs or if a remote
object has no MD5 metadata.

Usage:
    python scripts/pron_integrity_check.py \
        --audio-dir data/pron/donor_audio \
        --shortlist data/pron/pron_shortlist_v2.csv \
        --out data/pron/integrity_check.json
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor

BUCKET = "sheikh-fitzgerald-backup"
PREFIX = "ARABIC_DATA/QURAN_VERSE_BY_VERSE_RECITATIONS_DATASETS"
DEFAULT_RECITERS_FILE = "reciters_shortlist/shortlist.txt"
DEFAULT_RECITERS_REF = "main"


def load_keys(shortlist: str) -> list[str]:
    with open(shortlist, encoding="utf-8", newline="") as fh:
        return [row["key"] for row in csv.DictReader(fh)]


def read_reciters(reciters_file: str, git_ref: str) -> list[str]:
    if os.path.exists(reciters_file):
        with open(reciters_file, encoding="utf-8") as fh:
            text = fh.read()
    else:
        text = subprocess.run(
            ["git", "show", f"{git_ref}:{reciters_file}"],
            capture_output=True, text=True, check=True,
        ).stdout
    return [
        os.path.basename(line.strip().rstrip("/"))
        for line in text.splitlines() if line.strip()
    ]


def local_md5(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def remote_md5s(reciters: list[str]) -> dict[tuple[str, str], str | None]:
    """Map (reciter, key) -> hex md5 from GCS metadata (listing only).

    Uses ``gcloud storage ls --json`` (gcloud's authenticated user creds)
    because the object metadata ``md5Hash`` is enough; nothing is downloaded.
    """
    out: dict[tuple[str, str], str | None] = {}
    for reciter in reciters:
        uri = f"gs://{BUCKET}/{PREFIX}/{reciter}/"
        proc = subprocess.run(
            ["gcloud", "storage", "ls", "--json", uri],
            capture_output=True, text=True, check=True,
        )
        for entry in json.loads(proc.stdout):
            if entry.get("type") != "cloud_object":
                continue
            meta = entry["metadata"]
            name = meta["name"]
            if not name.endswith(".mp3"):
                continue
            key = os.path.basename(name)[:-4]
            md5 = meta.get("md5Hash")
            out[(reciter, key)] = base64.b64decode(md5).hex() if md5 else None
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--audio-dir", default="data/pron/donor_audio")
    ap.add_argument("--shortlist", default="data/pron/pron_shortlist_v2.csv")
    ap.add_argument("--out", default="data/pron/integrity_check.json")
    ap.add_argument("--reciters-file", default=DEFAULT_RECITERS_FILE)
    ap.add_argument("--reciters-ref", default=DEFAULT_RECITERS_REF)
    ap.add_argument("--workers", type=int, default=min(8, os.cpu_count() or 1))
    args = ap.parse_args(argv)

    keys = load_keys(args.shortlist)
    reciters = read_reciters(args.reciters_file, args.reciters_ref)

    print(f"listing GCS metadata for {len(reciters)} reciter prefixes ...")
    remote = remote_md5s(reciters)
    print(f"remote md5 entries: {len(remote)}")

    tasks = []
    missing_local = []
    for reciter in reciters:
        for key in keys:
            path = os.path.join(args.audio_dir, reciter, f"{key}.mp3")
            if os.path.exists(path):
                tasks.append((reciter, key, path))
            else:
                missing_local.append({"reciter": reciter, "key": key})

    def worker(task: tuple[str, str, str]) -> tuple[str, str, str]:
        reciter, key, path = task
        return reciter, key, local_md5(path)

    files: list[dict] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for reciter, key, lmd5 in pool.map(worker, tasks):
            rmd5 = remote.get((reciter, key))
            files.append({
                "reciter": reciter,
                "key": key,
                "local_md5": lmd5,
                "remote_md5": rmd5,
                "match": rmd5 is not None and lmd5 == rmd5,
            })
    files.sort(key=lambda f: (f["reciter"], f["key"]))

    mismatched = [f for f in files if not f["match"]]
    no_remote = [f for f in files if f["remote_md5"] is None]
    doc = {
        "bucket": BUCKET,
        "prefix": PREFIX,
        "audio_root": os.path.abspath(args.audio_dir),
        "expected_files": len(keys) * len(reciters),
        "checked": len(files),
        "matched": len(files) - len(mismatched),
        "mismatched_count": len(mismatched),
        "remote_missing_md5_count": len(no_remote),
        "missing_local": missing_local,
        "all_match": (not mismatched and not missing_local
                      and len(files) == len(keys) * len(reciters)),
        "mismatches": [
            {"reciter": f["reciter"], "key": f["key"],
             "local_md5": f["local_md5"], "remote_md5": f["remote_md5"]}
            for f in mismatched
        ],
        "files": files,
    }
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2)

    print(f"checked        : {doc['checked']}/{doc['expected_files']}")
    print(f"matched        : {doc['matched']}")
    print(f"mismatched     : {doc['mismatched_count']}")
    print(f"missing remote : {doc['remote_missing_md5_count']}")
    print(f"missing local  : {len(missing_local)}")
    print(f"ALL MATCH      : {doc['all_match']}")
    if mismatched:
        for f in mismatched:
            print(f"  MISMATCH {f['reciter']}/{f['key']} "
                  f"local={f['local_md5']} remote={f['remote_md5']}")
    print(f"\nwritten: {args.out}")
    return 0 if doc["all_match"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
