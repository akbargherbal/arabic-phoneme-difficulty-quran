#!/usr/bin/env python3
"""Content-level audio quality gate keeping for the full donor corpus.

The ffprobe pass (``pron_donor_manifest.py``) already verified the container /
codec / sample-rate metadata of all 3,150 donor mp3s.  This pass decodes the
actual waveform with ``librosa`` (mono, native sample rate, no resampling) and
looks for problems ffprobe cannot see:

* **clipping** - fraction of samples at/above 0.99 full scale, and the longest
  run of consecutive such samples.
* **silence** - frame-wise RMS in dBFS: share of frames below -50 dBFS, plus the
  leading / trailing continuous silence in seconds.
* **bandwidth** - median spectral roll-off (0.95 energy) in Hz.

Raw metrics are always recorded; a file is *flagged* only when a metric crosses
its threshold:

======================  =========================================
flag                    condition
======================  =========================================
``clipping``            ``clip_fraction > 0.001`` AND ``clip_max_run >= 3``
``silence``             ``>20%`` frames < -50 dBFS OR leading >2.0 s OR
                        trailing >2.0 s
``bandwidth``           median roll-off < 10000 Hz
======================  =========================================

This is a read-only analysis tool: it never modifies or deletes audio.

Usage:
    python scripts/pron_audio_quality.py \
        --audio-dir data/pron/donor_audio \
        --shortlist data/pron/pron_shortlist_v2.csv \
        --out data/pron/audio_quality_report.json
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
from concurrent.futures import ProcessPoolExecutor

import librosa
import numpy as np
import soundfile as sf

DEFAULT_RECITERS_FILE = "reciters_shortlist/shortlist.txt"
DEFAULT_RECITERS_REF = "main"

CLIP_LEVEL = 0.99
CLIP_FRACTION_THRESHOLD = 0.001
CLIP_RUN_THRESHOLD = 3
SILENCE_DBFS = -50.0
SILENCE_FRAME_PCT_THRESHOLD = 20.0
SILENCE_EDGE_S_THRESHOLD = 2.0
ROLLOFF_THRESHOLD_HZ = 10000.0
FRAME_LENGTH = 2048
HOP_LENGTH = 512


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


def longest_run(mask: np.ndarray) -> int:
    """Length of the longest run of True in a boolean array."""
    if not mask.any():
        return 0
    padded = np.concatenate(([False], mask, [False]))
    edges = np.flatnonzero(np.diff(padded.astype(np.int8)))
    return int((edges[1::2] - edges[::2]).max())


def compute_metrics(y: np.ndarray, sr: int) -> dict:
    """Compute the content metrics + flags for one decoded mono waveform."""
    # --- clipping ---------------------------------------------------------
    clip_mask = np.abs(y) >= CLIP_LEVEL
    clip_fraction = float(clip_mask.mean())
    clip_max_run = longest_run(clip_mask)

    # --- silence (frame-wise RMS in dBFS) ---------------------------------
    rms = librosa.feature.rms(
        y=y, frame_length=FRAME_LENGTH, hop_length=HOP_LENGTH, center=True
    )[0]
    db = librosa.amplitude_to_db(rms, ref=1.0)
    silent = db < SILENCE_DBFS
    silence_frame_pct = float(silent.mean() * 100.0)

    hop_s = HOP_LENGTH / sr
    n_lead = int(np.argmax(~silent)) if silent.any() and not silent.all() else 0
    if silent.all():
        n_lead = int(silent.size)
    n_trail = 0
    if silent.any() and not silent.all():
        n_trail = int(np.argmax(~silent[::-1]))
    elif silent.all():
        n_trail = int(silent.size)
    leading_silence_s = n_lead * hop_s
    trailing_silence_s = n_trail * hop_s

    # --- bandwidth (median 0.95 spectral roll-off) ------------------------
    rolloff = librosa.feature.spectral_rolloff(
        y=y, sr=sr, roll_percent=0.95, n_fft=FRAME_LENGTH,
        hop_length=HOP_LENGTH, center=True,
    )[0]
    rolloff_median_hz = float(np.median(rolloff))

    flags: list[str] = []
    if clip_fraction > CLIP_FRACTION_THRESHOLD and clip_max_run >= CLIP_RUN_THRESHOLD:
        flags.append("clipping")
    if (silence_frame_pct > SILENCE_FRAME_PCT_THRESHOLD
            or leading_silence_s > SILENCE_EDGE_S_THRESHOLD
            or trailing_silence_s > SILENCE_EDGE_S_THRESHOLD):
        flags.append("silence")
    if rolloff_median_hz < ROLLOFF_THRESHOLD_HZ:
        flags.append("bandwidth")

    return {
        "sample_rate": int(sr),
        "duration_s": round(float(y.size) / sr, 6),
        "clip_fraction": round(clip_fraction, 8),
        "clip_max_run": clip_max_run,
        "silence_frame_pct": round(silence_frame_pct, 4),
        "leading_silence_s": round(leading_silence_s, 4),
        "trailing_silence_s": round(trailing_silence_s, 4),
        "rolloff_median_hz": round(rolloff_median_hz, 2),
        "flags": flags,
    }


def analyze(path: str) -> dict:
    """Decode one file (mono, native rate) and return its metrics + flags.

    Prefers libsndfile; if that fails we fall back to librosa/audioread and
    record the fallback + decode error, since a resynced MPEG stream is itself a
    content-level quality signal that the format pass cannot see.
    """
    backend = "soundfile"
    decode_error = None
    try:
        y, sr = sf.read(path, dtype="float32", always_2d=False)
        if y.ndim > 1:
            y = y.mean(axis=1)
    except Exception as exc:  # noqa: BLE001 - fall back, record the reason
        backend = "audioread_fallback"
        decode_error = f"{type(exc).__name__}: {exc}"
        y, sr = librosa.load(path, sr=None, mono=True)
    if y.size == 0:
        return {"error": "decoded zero samples"}

    metrics = compute_metrics(np.asarray(y, dtype=np.float32), int(sr))
    metrics["decode_backend"] = backend
    metrics["decode_error"] = decode_error
    return metrics


def _worker(task: tuple[str, str, str]) -> tuple[str, str, dict]:
    reciter, key, path = task
    try:
        return reciter, key, analyze(path)
    except Exception as exc:  # noqa: BLE001 - record, never abort the run
        return reciter, key, {"error": f"{type(exc).__name__}: {exc}"}


def _median(values: list[float]) -> float | None:
    return round(float(np.median(values)), 4) if values else None


def format_anomaly_crossref(
    manifest: dict, files: list[dict]
) -> tuple[list[dict], dict]:
    """Cross-reference manifest format anomalies against this pass's flags."""
    content = {(f["reciter"], f["key"]): f for f in files}
    rows: list[dict] = []
    also_flagged = 0
    for reciter, rec in manifest.get("per_reciter", {}).items():
        for anomaly in rec.get("unexpected_format_files", []):
            key = anomaly["key"]
            f = content.get((reciter, key))
            flags = f["flags"] if f else None
            hit = bool(flags)
            also_flagged += int(hit)
            rows.append({
                "reciter": reciter,
                "key": key,
                "format_issue": {
                    "sample_rate": anomaly["sample_rate"],
                    "channels": anomaly["channels"],
                    "bit_rate": anomaly["bit_rate"],
                },
                "content_flagged": hit,
                "content_flags": flags,
                "rolloff_median_hz": f["rolloff_median_hz"] if f else None,
                "silence_frame_pct": f["silence_frame_pct"] if f else None,
                "clip_fraction": f["clip_fraction"] if f else None,
            })
    summary = {
        "format_anomaly_files": len(rows),
        "also_flagged_by_content_check": also_flagged,
        "not_flagged_by_content_check": len(rows) - also_flagged,
    }
    return rows, summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--audio-dir", default="data/pron/donor_audio")
    ap.add_argument("--shortlist", default="data/pron/pron_shortlist_v2.csv")
    ap.add_argument("--out", default="data/pron/audio_quality_report.json")
    ap.add_argument("--reciters-file", default=DEFAULT_RECITERS_FILE)
    ap.add_argument("--reciters-ref", default=DEFAULT_RECITERS_REF)
    ap.add_argument("--manifest", default="data/pron/donor_audio_manifest.json")
    ap.add_argument("--workers", type=int, default=min(8, os.cpu_count() or 1))
    args = ap.parse_args(argv)

    keys = load_keys(args.shortlist)
    reciters = read_reciters(args.reciters_file, args.reciters_ref)

    tasks: list[tuple[str, str, str]] = []
    missing: list[dict] = []
    for reciter in reciters:
        for key in keys:
            path = os.path.join(args.audio_dir, reciter, f"{key}.mp3")
            if os.path.exists(path):
                tasks.append((reciter, key, path))
            else:
                missing.append({"reciter": reciter, "key": key})

    results: list[dict] = []
    failures: list[dict] = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for reciter, key, info in pool.map(_worker, tasks, chunksize=4):
            if "error" in info:
                failures.append({"reciter": reciter, "key": key,
                                 "reason": info["error"]})
                continue
            info.update({"reciter": reciter, "key": key})
            results.append(info)

    results.sort(key=lambda f: (f["reciter"], f["key"]))
    flag_types = ["clipping", "silence", "bandwidth"]
    per_flag_counts = {t: 0 for t in flag_types}
    per_reciter: dict[str, dict] = {
        r: {"checked": 0, "flagged_any": 0, **{t: 0 for t in flag_types},
            "failed": 0}
        for r in reciters
    }
    for f in results:
        rec = per_reciter[f["reciter"]]
        rec["checked"] += 1
        if f["flags"]:
            rec["flagged_any"] += 1
        for t in f["flags"]:
            rec[t] += 1
            per_flag_counts[t] += 1
    for fail in failures:
        per_reciter[fail["reciter"]]["failed"] += 1

    flagged_files = [f for f in results if f["flags"]]
    fallbacks = [
        {"reciter": f["reciter"], "key": f["key"],
         "decode_backend": f["decode_backend"], "decode_error": f["decode_error"]}
        for f in results if f.get("decode_backend") == "audioread_fallback"
    ]
    format_rows: list[dict] = []
    format_summary = {"format_anomaly_files": 0,
                      "also_flagged_by_content_check": 0,
                      "not_flagged_by_content_check": 0}
    if os.path.exists(args.manifest):
        with open(args.manifest, encoding="utf-8") as fh:
            manifest = json.load(fh)
        format_rows, format_summary = format_anomaly_crossref(manifest, results)

    per_reciter_summary = {}
    for r in reciters:
        rec = per_reciter[r]
        per_reciter_summary[r] = rec

    # Reciters ranked by how many of their files tripped any check.
    reciter_rank = sorted(
        ((r, per_reciter[r]["flagged_any"]) for r in reciters),
        key=lambda kv: kv[1], reverse=True,
    )

    doc = {
        "audio_root": os.path.abspath(args.audio_dir),
        "shortlist": args.shortlist,
        "shortlist_count": len(keys),
        "reciters": reciters,
        "reciter_count": len(reciters),
        "expected_files": len(keys) * len(reciters),
        "files_checked": len(results),
        "missing_files": missing,
        "failures": failures,
        "thresholds": {
            "clip_level": CLIP_LEVEL,
            "clip_fraction": CLIP_FRACTION_THRESHOLD,
            "clip_run": CLIP_RUN_THRESHOLD,
            "silence_dbfs": SILENCE_DBFS,
            "silence_frame_pct": SILENCE_FRAME_PCT_THRESHOLD,
            "silence_edge_s": SILENCE_EDGE_S_THRESHOLD,
            "rolloff_hz": ROLLOFF_THRESHOLD_HZ,
            "frame_length": FRAME_LENGTH,
            "hop_length": HOP_LENGTH,
        },
        "summary": {
            "files_checked": len(results),
            "files_flagged_any": len(flagged_files),
            "files_clean": len(results) - len(flagged_files),
            "per_flag_type": per_flag_counts,
            "per_reciter": per_reciter_summary,
            "reciters_ranked_by_flags": [
                {"reciter": r, "flagged_any": n} for r, n in reciter_rank
            ],
            "rolloff_median_hz_overall": _median(
                [f["rolloff_median_hz"] for f in results]
            ),
            "decode_backend_fallback_count": len(fallbacks),
            "decode_backend_fallbacks": fallbacks,
        },
        "notes": {
            "bandwidth": (
                "The median 0.95-energy roll-off of speech sits at roughly "
                "3-6 kHz regardless of the true codec cutoff, so the "
                "<10000 Hz rule flagged every file (3150/3150) and is not "
                "discriminative. The raw rolloff_median_hz values are committed "
                "so a better-calibrated threshold can be chosen later."
            ),
            "decode_backend": (
                "Files whose libsndfile decode failed and fell back to "
                "librosa/audioread (summary.decode_backend_fallbacks) decoded "
                "only after MPEG resync warnings; libsndfile reports them as "
                "internally corrupt. These are invisible to the ffprobe pass."
            ),
        },
        "format_anomaly_crossref": {
            "summary": format_summary,
            "files": format_rows,
        },
        "files": results,
    }
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2)

    s = doc["summary"]
    print(f"checked        : {s['files_checked']}/{doc['expected_files']}")
    print(f"missing        : {len(missing)}")
    print(f"failures       : {len(failures)}")
    print(f"flagged (any)  : {s['files_flagged_any']}")
    print(f"  clipping     : {per_flag_counts['clipping']}")
    print(f"  silence      : {per_flag_counts['silence']}")
    print(f"  bandwidth    : {per_flag_counts['bandwidth']}")
    print(f"rolloff median : {s['rolloff_median_hz_overall']} Hz")
    print(f"decode fallback: {len(fallbacks)} (libsndfile -> audioread)")
    print("per reciter (flagged_any/checked):")
    for r, n in reciter_rank:
        print(f"  {r:<32} {n}/{per_reciter[r]['checked']}")
    print(f"format anomalies also flagged: "
          f"{format_summary['also_flagged_by_content_check']}/"
          f"{format_summary['format_anomaly_files']}")
    print(f"\nwritten: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
