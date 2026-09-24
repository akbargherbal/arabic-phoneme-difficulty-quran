#!/usr/bin/env python3
"""Report-only silence and loudness analysis for the full donor corpus.

Task 8.  Nothing here edits audio: it decodes each mp3, measures, and writes a
report.  Two unified checks:

* **silence_long_gap** - the longest single continuous run of silence anywhere
  in the file (same -50 dBFS frame-RMS rule as the earlier checker), flagged
  when that run is >= 3.0 s.  This replaces the Task 6/7 "20% of frames" and
  separate leading/trailing rules.
* **loudness_outlier** - integrated loudness in LUFS (ITU-R BS.1770 via
  pyloudnorm), flagged when a file deviates by more than 3.0 dB from *its own
  reciter's* median LUFS.  Cross-reciter loudness differences are reported
  informationally only, never flagged.

The content-level rolloff data and the retired-bandwidth decision from
Task 6/7 are carried over here unchanged; ``audio_quality_report.json`` is left
untouched.

Usage:
    python scripts/pron_audio_level_silence.py \
        --audio-dir data/pron/donor_audio \
        --shortlist data/pron/pron_shortlist_v2.csv \
        --out data/pron/audio_level_silence_report.json
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
from concurrent.futures import ProcessPoolExecutor

import librosa
import numpy as np
import pyloudnorm as pyln
import soundfile as sf

import pron_audio_quality as aq

DEFAULT_RECITERS_FILE = "reciters_shortlist/shortlist.txt"
DEFAULT_RECITERS_REF = "main"
SILENCE_LONG_GAP_S = 3.0
LOUDNESS_DEVIATION_DB = 3.0

BANDWIDTH_DECISION = (
    "retired - not discriminative for this content, see notes.bandwidth; "
    "sample_rate from ffprobe is the correct signal for under-sampling, not "
    "content rolloff"
)
BANDWIDTH_NOTE = (
    "The median 0.95-energy roll-off of speech sits at roughly 3-6 kHz "
    "regardless of the true codec cutoff, so the <10000 Hz rule flagged every "
    "file (3150/3150) and is not discriminative. Raw rolloff_median_hz values "
    "are carried over from Task 6 and committed so a better-calibrated "
    "threshold can be chosen later."
)


def decode_audio(path: str) -> tuple[np.ndarray, int, str, str | None]:
    """Decode to mono/native-rate; prefer libsndfile, fall back to audioread."""
    try:
        y, sr = sf.read(path, dtype="float32", always_2d=False)
        if y.ndim > 1:
            y = y.mean(axis=1)
        return y, int(sr), "soundfile", None
    except Exception as exc:  # noqa: BLE001
        y, sr = librosa.load(path, sr=None, mono=True)
        return y, int(sr), "audioread_fallback", f"{type(exc).__name__}: {exc}"


def longest_true_run(mask: np.ndarray) -> tuple[int, int]:
    """Return (length, start_index) of the longest run of True."""
    best_len = best_start = cur = start = 0
    for i, value in enumerate(mask.tolist()):
        if value:
            if cur == 0:
                start = i
            cur += 1
            if cur > best_len:
                best_len, best_start = cur, start
        else:
            cur = 0
    return best_len, best_start


def longest_silent_run(y: np.ndarray, sr: int) -> dict:
    """Longest continuous silent stretch and where it sits in the file."""
    mask = aq.edge_silence(y, sr)["silent"]
    n, start = longest_true_run(mask)
    hop_s = aq.HOP_LENGTH / sr
    return {
        "longest_silent_run_s": round(n * hop_s, 4),
        "n_silent_frames": n,
        "silent_run_position": {
            "start_s": round(start * hop_s, 4),
            "end_s": round((start + n) * hop_s, 4),
        },
    }


def integrated_lufs(y: np.ndarray, sr: int) -> float | None:
    """ITU-R BS.1770 integrated loudness in LUFS (None if non-finite)."""
    value = float(pyln.Meter(sr).integrated_loudness(np.asarray(y, dtype=float)))
    return value if np.isfinite(value) else None


def _worker(task: tuple[str, str, str]) -> tuple[str, str, dict]:
    reciter, key, path = task
    try:
        y, sr, backend, decode_error = decode_audio(path)
    except Exception as exc:  # noqa: BLE001
        return reciter, key, {"error": f"{type(exc).__name__}: {exc}"}
    if y.size == 0:
        return reciter, key, {"error": "decoded zero samples"}
    y = np.asarray(y, dtype=np.float32)

    clip_mask = np.abs(y) >= aq.CLIP_LEVEL
    rolloff = librosa.feature.spectral_rolloff(
        y=y, sr=sr, roll_percent=0.95, n_fft=aq.FRAME_LENGTH,
        hop_length=aq.HOP_LENGTH, center=True,
    )[0]

    lufs = None
    loudness_error = None
    try:
        lufs = integrated_lufs(y, sr)
    except Exception as exc:  # noqa: BLE001
        loudness_error = f"{type(exc).__name__}: {exc}"
    if lufs is None and loudness_error is None:
        loudness_error = "non-finite integrated loudness"

    run = longest_silent_run(y, sr)
    return reciter, key, {
        "sample_rate": int(sr),
        "duration_s": round(float(y.size) / sr, 6),
        "clip_fraction": round(float(clip_mask.mean()), 8),
        "clip_max_run": aq.longest_run(clip_mask),
        "rolloff_median_hz": round(float(np.median(rolloff)), 2),
        "longest_silent_run_s": run["longest_silent_run_s"],
        "n_silent_frames": run["n_silent_frames"],
        "silent_run_position": run["silent_run_position"],
        "lufs": round(lufs, 4) if lufs is not None else None,
        "loudness_error": loudness_error,
        "decode_backend": backend,
        "decode_error": decode_error,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--audio-dir", default="data/pron/donor_audio")
    ap.add_argument("--shortlist", default="data/pron/pron_shortlist_v2.csv")
    ap.add_argument("--out", default="data/pron/audio_level_silence_report.json")
    ap.add_argument("--reciters-file", default=DEFAULT_RECITERS_FILE)
    ap.add_argument("--reciters-ref", default=DEFAULT_RECITERS_REF)
    ap.add_argument("--workers", type=int, default=min(8, os.cpu_count() or 1))
    args = ap.parse_args(argv)

    keys = aq.load_keys(args.shortlist)
    reciters = aq.read_reciters(args.reciters_file, args.reciters_ref)

    tasks: list[tuple[str, str, str]] = []
    missing: list[dict] = []
    for reciter in reciters:
        for key in keys:
            path = os.path.join(args.audio_dir, reciter, f"{key}.mp3")
            if os.path.exists(path):
                tasks.append((reciter, key, path))
            else:
                missing.append({"reciter": reciter, "key": key})

    files: list[dict] = []
    failures: list[dict] = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for reciter, key, info in pool.map(_worker, tasks, chunksize=4):
            if "error" in info:
                failures.append({"reciter": reciter, "key": key,
                                 "reason": info["error"]})
                continue
            info.update({"reciter": reciter, "key": key})
            files.append(info)

    # Per-reciter loudness statistics from finite LUFS values only.
    per_reciter_lufs: dict[str, dict] = {}
    for reciter in reciters:
        vals = [f["lufs"] for f in files
                if f["reciter"] == reciter and f["lufs"] is not None]
        if vals:
            per_reciter_lufs[reciter] = {
                "n_finite": len(vals),
                "median_lufs": round(statistics.median(vals), 4),
                "mean_lufs": round(statistics.fmean(vals), 4),
                "std_lufs": round(statistics.stdev(vals), 4) if len(vals) > 1
                else 0.0,
                "min_lufs": round(min(vals), 4),
                "max_lufs": round(max(vals), 4),
            }
        else:
            per_reciter_lufs[reciter] = {
                "n_finite": 0, "median_lufs": None, "mean_lufs": None,
                "std_lufs": None, "min_lufs": None, "max_lufs": None,
            }

    flag_types = ["clipping", "silence_long_gap", "loudness_outlier"]
    per_flag_counts = {t: 0 for t in flag_types}
    per_reciter = {
        r: {"checked": 0, "flagged_any": 0, **{t: 0 for t in flag_types},
            "failed": 0}
        for r in reciters
    }
    files.sort(key=lambda f: (f["reciter"], f["key"]))
    for f in files:
        median = per_reciter_lufs[f["reciter"]]["median_lufs"]
        f["reciter_median_lufs"] = median
        f["deviation_db"] = (round(f["lufs"] - median, 4)
                             if f["lufs"] is not None and median is not None
                             else None)
        flags: list[str] = []
        if (f["clip_fraction"] > aq.CLIP_FRACTION_THRESHOLD
                and f["clip_max_run"] >= aq.CLIP_RUN_THRESHOLD):
            flags.append("clipping")
        if f["longest_silent_run_s"] >= SILENCE_LONG_GAP_S:
            flags.append("silence_long_gap")
        if (f["deviation_db"] is not None
                and abs(f["deviation_db"]) > LOUDNESS_DEVIATION_DB):
            flags.append("loudness_outlier")
        f["flags"] = flags

        rec = per_reciter[f["reciter"]]
        rec["checked"] += 1
        if flags:
            rec["flagged_any"] += 1
        for t in flags:
            rec[t] += 1
            per_flag_counts[t] += 1
    for fail in failures:
        per_reciter[fail["reciter"]]["failed"] += 1

    flagged_files = [f for f in files if f["flags"]]
    rank = sorted(
        ((r, per_reciter[r]["flagged_any"]) for r in reciters),
        key=lambda kv: kv[1], reverse=True,
    )
    rank_sil = sorted(
        ((r, per_reciter[r]["silence_long_gap"]) for r in reciters),
        key=lambda kv: kv[1], reverse=True,
    )
    rank_loud = sorted(
        ((r, per_reciter[r]["loudness_outlier"]) for r in reciters),
        key=lambda kv: kv[1], reverse=True,
    )
    longest = max((f["longest_silent_run_s"] for f in files), default=0.0)

    doc = {
        "audio_root": os.path.abspath(args.audio_dir),
        "shortlist": args.shortlist,
        "shortlist_count": len(keys),
        "reciters": reciters,
        "reciter_count": len(reciters),
        "expected_files": len(keys) * len(reciters),
        "files_checked": len(files),
        "missing_files": missing,
        "failures": failures,
        "thresholds": {
            "silence_dbfs": aq.SILENCE_DBFS,
            "silence_long_gap_s": SILENCE_LONG_GAP_S,
            "loudness_deviation_db": LOUDNESS_DEVIATION_DB,
            "clip_level": aq.CLIP_LEVEL,
            "clip_fraction": aq.CLIP_FRACTION_THRESHOLD,
            "clip_run": aq.CLIP_RUN_THRESHOLD,
            "frame_length": aq.FRAME_LENGTH,
            "hop_length": aq.HOP_LENGTH,
        },
        "decisions": {"bandwidth_flag": BANDWIDTH_DECISION},
        "notes": {
            "bandwidth": BANDWIDTH_NOTE,
            "policy": (
                "Report-only. No source mp3 was modified, trimmed, re-encoded "
                "or gain-adjusted; loudness_outlier is informational and is not "
                "corrected here."
            ),
        },
        "summary": {
            "files_checked": len(files),
            "files_flagged_any": len(flagged_files),
            "per_flag_type": per_flag_counts,
            "per_reciter": per_reciter,
            "reciters_ranked_by_flagged_any": [
                {"reciter": r, "flagged_any": n} for r, n in rank
            ],
            "reciters_ranked_by_silence_long_gap": [
                {"reciter": r, "silence_long_gap": n} for r, n in rank_sil
            ],
            "reciters_ranked_by_loudness_outlier": [
                {"reciter": r, "loudness_outlier": n} for r, n in rank_loud
            ],
            "per_reciter_lufs": per_reciter_lufs,
            "max_longest_silent_run_s": longest,
            "rolloff_median_hz_overall": round(
                float(np.median([f["rolloff_median_hz"] for f in files])), 4
            ) if files else None,
        },
        "files": files,
    }
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2)

    s = doc["summary"]
    print(f"checked           : {s['files_checked']}/{doc['expected_files']}")
    print(f"missing           : {len(missing)}")
    print(f"failures          : {len(failures)}")
    print(f"flagged (any)     : {s['files_flagged_any']}")
    print(f"  clipping        : {per_flag_counts['clipping']}")
    print(f"  silence_long_gap: {per_flag_counts['silence_long_gap']}")
    print(f"  loudness_outlier: {per_flag_counts['loudness_outlier']}")
    print(f"longest silent run: {s['max_longest_silent_run_s']} s")
    print("per reciter (checked | silence_long_gap | loudness_outlier | "
          "flagged_any | median LUFS):")
    for r in reciters:
        rec = per_reciter[r]
        med = per_reciter_lufs[r]["median_lufs"]
        print(f"  {r:<32} {rec['checked']:>4} | {rec['silence_long_gap']:>4} | "
              f"{rec['loudness_outlier']:>4} | {rec['flagged_any']:>4} | "
              f"{med}")
    print(f"\nwritten: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
