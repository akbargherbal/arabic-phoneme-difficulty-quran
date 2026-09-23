#!/usr/bin/env python3
"""Probe the scratch download of the shortlisted per-verse mp3s.

Downloads (if absent) 3 shortlisted ayat x 2 full-coverage reciters into a
scratch directory — never inside the repo — then runs ``ffprobe`` and writes
the duration / sample-rate / channel / bitrate numbers to a committed JSON so
the values live in version control instead of only in a chat reply.

Usage:
    python scripts/pron_ffprobe_scratch.py \
        --out data/pron/ffprobe_scratch.json \
        --scratch /tmp/opencode/audio_audit
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess

BUCKET = (
    "gs://sheikh-fitzgerald-backup/ARABIC_DATA/"
    "QURAN_VERSE_BY_VERSE_RECITATIONS_DATASETS"
)
DEFAULT_RECITERS = ["Husary_Muallim_128kbps", "Minshawy_Murattal_128kbps"]
DEFAULT_KEYS = ["068030", "026148", "023041"]  # A, A, B


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=True)


def ensure_local(reciter: str, key: str, scratch: str) -> str:
    path = os.path.join(scratch, f"{reciter}__{key}.mp3")
    if not os.path.exists(path):
        os.makedirs(scratch, exist_ok=True)
        run(["gcloud", "storage", "cp", f"{BUCKET}/{reciter}/{key}.mp3", path])
    return path


def probe(path: str) -> dict:
    out = run([
        "ffprobe", "-v", "error", "-select_streams", "a:0",
        "-show_entries", "stream=codec_name,sample_rate,channels,bit_rate",
        "-show_entries", "format=duration,size",
        "-print_format", "json", path,
    ]).stdout
    data = json.loads(out)
    stream = data["streams"][0]
    fmt = data["format"]
    return {
        "codec": stream.get("codec_name"),
        "sample_rate": int(stream["sample_rate"]),
        "channels": int(stream["channels"]),
        "bit_rate": int(stream["bit_rate"]) if stream.get("bit_rate") else None,
        "duration_s": round(float(fmt["duration"]), 6),
        "bytes": int(fmt["size"]),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="data/pron/ffprobe_scratch.json")
    ap.add_argument("--scratch", default="/tmp/opencode/audio_audit")
    ap.add_argument("--shortlist", default="data/pron/pron_shortlist_v2.csv")
    ap.add_argument("--reciters", nargs="+", default=DEFAULT_RECITERS)
    ap.add_argument("--keys", nargs="+", default=DEFAULT_KEYS)
    args = ap.parse_args(argv)

    with open(args.shortlist, encoding="utf-8", newline="") as fh:
        length_type = {r["key"]: r["length_type"] for r in csv.DictReader(fh)}

    files = []
    for reciter in args.reciters:
        for key in args.keys:
            path = ensure_local(reciter, key, args.scratch)
            entry = {
                "reciter": reciter,
                "key": key,
                "length_type": length_type.get(key),
                "file": os.path.basename(path),
                **probe(path),
            }
            files.append(entry)

    doc = {
        "bucket": BUCKET,
        "scratch_dir": args.scratch,
        "note": "ffprobe of the Part C step 8 scratch download; audio not committed.",
        "reciters": args.reciters,
        "keys": args.keys,
        "files": files,
    }
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2)

    for e in files:
        print(
            f"{e['file']:<44} dur={e['duration_s']:<12} "
            f"sr={e['sample_rate']} ch={e['channels']} "
            f"bitrate={e['bit_rate']} {e['codec']}"
        )
    print(f"\nwritten: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
