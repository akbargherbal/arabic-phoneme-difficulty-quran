#!/usr/bin/env python3
"""ffprobe the full donor corpus and commit a summary manifest.

Walks ``<audio-dir>/<reciter>/<SSSAAA>.mp3`` for every reciter x shortlist key,
runs ``ffprobe`` on every file (not a sample), and writes a machine-readable
summary to ``data/pron/donor_audio_manifest.json``: per-reciter file count,
total duration, distinct format fields, plus any missing / failed / 0-duration
/ corrupt files.  Unexpected values (duration out of band, mixed sample rates,
etc.) are collected under ``flagged_issues`` rather than averaged away.

Usage:
    python scripts/pron_donor_manifest.py \
        --audio-dir data/pron/donor_audio \
        --shortlist data/pron/pron_shortlist_v2.csv \
        --out data/pron/donor_audio_manifest.json
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor

BUCKET = (
    "gs://sheikh-fitzgerald-backup/ARABIC_DATA/"
    "QURAN_VERSE_BY_VERSE_RECITATIONS_DATASETS"
)
DEFAULT_RECITERS_FILE = "reciters_shortlist/shortlist.txt"
DEFAULT_RECITERS_REF = "main"
EXPECTED_SAMPLE_RATE = 44100
EXPECTED_CHANNELS = 2
DURATION_BAND = (1.0, 120.0)  # seconds outside this is flagged


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


def probe(path: str) -> dict:
    out = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "a:0",
            "-show_entries", "stream=codec_name,sample_rate,channels,bit_rate",
            "-show_entries", "format=duration,size",
            "-print_format", "json", path,
        ],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        return {"error": out.stderr.strip() or f"ffprobe rc={out.returncode}"}
    data = json.loads(out.stdout)
    if not data.get("streams"):
        return {"error": "no audio stream"}
    stream = data["streams"][0]
    fmt = data.get("format", {})
    try:
        duration = float(fmt["duration"])
    except (KeyError, ValueError, TypeError):
        return {"error": "no duration in format"}
    return {
        "codec": stream.get("codec_name"),
        "sample_rate": int(stream["sample_rate"]),
        "channels": int(stream["channels"]),
        "bit_rate": int(stream["bit_rate"]) if stream.get("bit_rate") else None,
        "duration_s": round(duration, 6),
        "bytes": int(fmt["size"]) if fmt.get("size") else None,
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--audio-dir", default="data/pron/donor_audio")
    ap.add_argument("--shortlist", default="data/pron/pron_shortlist_v2.csv")
    ap.add_argument("--out", default="data/pron/donor_audio_manifest.json")
    ap.add_argument("--reciters-file", default=DEFAULT_RECITERS_FILE)
    ap.add_argument("--reciters-ref", default=DEFAULT_RECITERS_REF)
    ap.add_argument("--workers", type=int, default=16)
    args = ap.parse_args(argv)

    keys = load_keys(args.shortlist)
    reciters = read_reciters(args.reciters_file, args.reciters_ref)

    tasks = []
    missing: list[dict] = []
    missing_keys: dict[str, set[str]] = {r: set() for r in reciters}
    for reciter in reciters:
        for key in keys:
            path = os.path.join(args.audio_dir, reciter, f"{key}.mp3")
            if os.path.exists(path):
                tasks.append((reciter, key, path))
            else:
                missing.append({"reciter": reciter, "key": key})
                missing_keys[reciter].add(key)

    def worker(task: tuple[str, str, str]) -> tuple[str, str, dict]:
        reciter, key, path = task
        return reciter, key, probe(path)

    results: dict[str, dict] = {r: {} for r in reciters}
    failures: list[dict] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for reciter, key, info in pool.map(worker, tasks):
            if "error" in info:
                failures.append({"reciter": reciter, "key": key,
                                 "reason": info["error"]})
                continue
            if info["duration_s"] <= 0:
                failures.append({"reciter": reciter, "key": key,
                                 "reason": f"non-positive duration "
                                           f"{info['duration_s']}"})
                continue
            results[reciter][key] = info

    per_reciter: dict[str, dict] = {}
    flagged: list[str] = []
    all_durations: list[float] = []
    overall_sr: set[int] = set()
    overall_ch: set[int] = set()
    overall_br: set[int] = set()
    overall_codec: set[str] = set()

    for reciter in reciters:
        info = results[reciter]
        durations = [v["duration_s"] for v in info.values()]
        all_durations.extend(durations)
        srs = sorted({v["sample_rate"] for v in info.values()})
        chs = sorted({v["channels"] for v in info.values()})
        brs = sorted({v["bit_rate"] for v in info.values() if v["bit_rate"]})
        codecs = sorted({v["codec"] for v in info.values() if v["codec"]})
        overall_sr.update(srs)
        overall_ch.update(chs)
        overall_br.update(brs)
        overall_codec.update(codecs)

        miss_keys = sorted(missing_keys[reciter])
        rec_failures = [f for f in failures if f["reciter"] == reciter]
        fmt_counts: dict[str, int] = {}
        expected_br = 192000 if "192kbps" in reciter else 128000
        unexpected_files = []
        for k in sorted(info):
            v = info[k]
            if (v["codec"] != "mp3" or v["sample_rate"] != EXPECTED_SAMPLE_RATE
                    or v["channels"] != EXPECTED_CHANNELS
                    or (v["bit_rate"] is not None and v["bit_rate"] != expected_br)):
                unexpected_files.append({
                    "key": k, "codec": v["codec"],
                    "sample_rate": v["sample_rate"], "channels": v["channels"],
                    "bit_rate": v["bit_rate"], "duration_s": v["duration_s"],
                })
        for v in info.values():
            sig = f"{v['codec']}/{v['sample_rate']}Hz/{v['channels']}ch/{v['bit_rate']}"
            fmt_counts[sig] = fmt_counts.get(sig, 0) + 1
        rec = {
            "expected": len(keys),
            "count": len(info),
            "missing_count": len(miss_keys),
            "missing_keys": miss_keys,
            "failure_count": len(rec_failures),
            "failures": rec_failures,
            "format_breakdown": dict(sorted(fmt_counts.items())),
            "unexpected_format_files": unexpected_files,
            "codecs": codecs,
            "sample_rates": srs,
            "channels": chs,
            "bit_rates": brs,
            **_stats(durations),
        }
        per_reciter[reciter] = rec

        if unexpected_files:
            flagged.append(
                f"{reciter}: {len(unexpected_files)} files differ from the "
                f"dir-implied format (mp3/{EXPECTED_SAMPLE_RATE}Hz/"
                f"{EXPECTED_CHANNELS}ch/{expected_br}bps); "
                f"see unexpected_format_files")
        if miss_keys:
            flagged.append(f"{reciter}: {len(miss_keys)} missing files")
        if rec_failures:
            flagged.append(f"{reciter}: {len(rec_failures)} failed/0-dur files")
        out_of_band = [k for k, v in info.items()
                       if not (DURATION_BAND[0] <= v["duration_s"]
                               <= DURATION_BAND[1])]
        if out_of_band:
            flagged.append(
                f"{reciter}: {len(out_of_band)} files outside "
                f"{DURATION_BAND[0]}-{DURATION_BAND[1]}s: "
                f"{sorted(out_of_band)[:10]}")

    doc = {
        "bucket": BUCKET,
        "audio_root": os.path.abspath(args.audio_dir),
        "shortlist": args.shortlist,
        "shortlist_count": len(keys),
        "reciters": reciters,
        "reciter_count": len(reciters),
        "expected_files": len(keys) * len(reciters),
        "files_probed": sum(len(v) for v in results.values()),
        "missing_files": missing,
        "failures": failures,
        "per_reciter": per_reciter,
        "totals": _stats(all_durations),
        "distinct_format": {
            "codecs": sorted(overall_codec),
            "sample_rates": sorted(overall_sr),
            "channels": sorted(overall_ch),
            "bit_rates": sorted(overall_br),
        },
        "duration_band_s": list(DURATION_BAND),
        "flagged_issues": flagged,
    }
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2)

    print(f"probed         : {doc['files_probed']}/{doc['expected_files']}")
    print(f"missing        : {len(missing)}")
    print(f"failures       : {len(failures)}")
    print(f"total duration : {doc['totals']['total_duration_s']} s")
    print(f"flagged issues : {len(flagged)}")
    for f in flagged:
        print(f"  - {f}")
    for reciter, rec in per_reciter.items():
        print(f"  {reciter:<32} {rec['count']}/{rec['expected']} "
              f"total={rec['total_duration_s']}s")
    print(f"\nwritten: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
