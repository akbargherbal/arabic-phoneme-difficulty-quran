#!/usr/bin/env python3
"""Probe per-ayah durations across all 9 shortlisted reciters.

Downloads (if absent) a fixed 6-key sample of per-verse mp3s for every
reciter listed in ``reciters_shortlist/shortlist.txt`` into a scratch
directory — never inside the repo — then runs ``ffprobe`` and writes the
duration / sample-rate / channel / bitrate numbers, plus an aggregate
summary, to a committed JSON.  The mp3s themselves are deleted after
probing so no audio ends up in the repo.

Sample is fixed at seed=42 and spans the two word-count bands:
    Type A (4-6 words):  029015, 037027, 068030
    Type B (7-12 words): 004021, 029037, 043067

Usage:
    python scripts/pron_reciter_duration_probe.py \
        --shortlist reciters_shortlist/shortlist.txt \
        --out data/pron/reciter_duration_probe.json \
        --scratch /tmp/opencode/reciter_duration_probe
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess

BUCKET = (
    "gs://sheikh-fitzgerald-backup/ARABIC_DATA/"
    "QURAN_VERSE_BY_VERSE_RECITATIONS_DATASETS"
)
SEED = 42
KEYS = ["029015", "037027", "068030", "004021", "029037", "043067"]
LENGTH_TYPE = {
    "029015": "A", "037027": "A", "068030": "A",
    "004021": "B", "029037": "B", "043067": "B",
}


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=True)


def read_reciters(shortlist: str) -> list[str]:
    """Return the reciter directory basenames from the shortlist file."""
    reciters = []
    with open(shortlist, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                reciters.append(os.path.basename(line.rstrip("/")))
    return reciters


def ensure_local(reciter: str, key: str, scratch: str) -> str:
    """Download one mp3 into scratch/<reciter>/<key>.mp3 if absent."""
    dest_dir = os.path.join(scratch, reciter)
    path = os.path.join(dest_dir, f"{key}.mp3")
    if not os.path.exists(path):
        os.makedirs(dest_dir, exist_ok=True)
        run([
            "gcloud", "storage", "cp", "-n",
            f"{BUCKET}/{reciter}/{key}.mp3", path,
        ])
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


def _stats(durations: list[float]) -> dict:
    n = len(durations)
    if not n:
        return {"n": 0, "mean_duration_s": None, "min_duration_s": None,
                "max_duration_s": None, "total_duration_s": None}
    return {
        "n": n,
        "mean_duration_s": round(sum(durations) / n, 6),
        "min_duration_s": round(min(durations), 6),
        "max_duration_s": round(max(durations), 6),
        "total_duration_s": round(sum(durations), 6),
    }


def summarize(files: list[dict]) -> dict:
    """Average duration by length type, per reciter and pooled."""
    per_reciter: dict[str, dict] = {}
    for rec in sorted({f["reciter"] for f in files}):
        rows = [f for f in files if f["reciter"] == rec]
        per_reciter[rec] = {
            "type_A": _stats([f["duration_s"] for f in rows
                              if f["length_type"] == "A"]),
            "type_B": _stats([f["duration_s"] for f in rows
                              if f["length_type"] == "B"]),
            "overall": _stats([f["duration_s"] for f in rows]),
        }
    pooled = {
        "type_A": _stats([f["duration_s"] for f in files
                          if f["length_type"] == "A"]),
        "type_B": _stats([f["duration_s"] for f in files
                          if f["length_type"] == "B"]),
        "overall": _stats([f["duration_s"] for f in files]),
    }
    return {"per_reciter": per_reciter, "pooled_all_reciters": pooled}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="data/pron/reciter_duration_probe.json")
    ap.add_argument("--scratch", default="/tmp/opencode/reciter_duration_probe")
    ap.add_argument("--shortlist", default="reciters_shortlist/shortlist.txt")
    ap.add_argument("--keep-audio", action="store_true",
                    help="skip deletion of the scratch mp3s after probing")
    args = ap.parse_args(argv)

    reciters = read_reciters(args.shortlist)

    files = []
    for reciter in reciters:
        for key in KEYS:
            path = ensure_local(reciter, key, args.scratch)
            entry = {
                "reciter": reciter,
                "key": key,
                "length_type": LENGTH_TYPE[key],
                "file": f"{reciter}/{key}.mp3",
                **probe(path),
            }
            files.append(entry)

    doc = {
        "bucket": BUCKET,
        "scratch_dir": args.scratch,
        "note": ("ffprobe of the Task 4 duration probe across all 9 "
                 "shortlisted reciters; audio not committed."),
        "seed": SEED,
        "reciters": reciters,
        "keys": KEYS,
        "key_length_types": LENGTH_TYPE,
        "files": files,
        "summary": summarize(files),
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

    if not args.keep_audio:
        shutil.rmtree(args.scratch, ignore_errors=True)
        print(f"deleted scratch audio: {args.scratch}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
