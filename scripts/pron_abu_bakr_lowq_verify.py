#!/usr/bin/env python3
"""Verify whether the 14 low-format Abu_Bakr donor files are low quality.

Task 12.  Read-only: every file is decoded in memory with torchaudio (mixed to
mono, native sample rate); nothing is resampled to disk or altered.

The 14 files in Abu_Bakr_Ash-Shaatree_128kbps encoded at 11025 Hz / mono /
24 kbps (vs 44100 Hz / stereo / 128 kbps for the other 336) are compared against
that 336-file reference set on four metrics, then classified with a rule fixed
in advance (see RULE_TEXT below).

Usage:
    python scripts/pron_abu_bakr_lowq_verify.py \
        --out data/pron/abu_bakr_lowq_verification.json
"""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import torchaudio
from scipy.signal import welch

RECITER = "Abu_Bakr_Ash-Shaatree_128kbps"
LOW_SAMPLE_RATE = 11025
FRAME_S = 0.025
NOISE_TOP_FRACTION = 0.05
CUTOFF_ABOVE_DB = 40.0
HF_BAND = (4000.0, 5500.0)
LOW_BAND = (300.0, 2000.0)
CONSONANT_BAND = (2000.0, 5000.0)

RULE_TEXT = (
    "Fixed in advance, per file: DISQUALIFY if the file is below the reference "
    "5th percentile on metric 2b (hf_ratio_4k_5p5k_db) or 2d "
    "(consonant_proxy_db) AND below the reference 5th percentile on at least "
    "one of 2a (bandwidth_cutoff_hz) or 2c (snr_proxy_db); KEEP if inside the "
    "reference 5th-95th range on both 2b and 2d; INCONCLUSIVE otherwise. "
    "'Below'/'inside' use the reference percentiles computed from the 336 "
    "44100 Hz files."
)

METRIC_DEFINITIONS = {
    "bandwidth_cutoff_hz": (
        "From the long-term average power spectral density (scipy.signal.welch, "
        "Hann, nperseg=2048, 50% overlap, density scaling), the highest "
        "frequency whose PSD is more than 40 dB above the file's own noise "
        "floor."
    ),
    "noise_floor": (
        "Median PSD (power) of the highest-frequency 5% of bins up to and "
        "including Nyquist."
    ),
    "hf_ratio_4k_5p5k_db": (
        "10*log10(sum(PSD in 4000-5500 Hz) / sum(PSD over 0-Nyquist)). The band "
        "is capped at 5.5 kHz for BOTH groups so the comparison is like for "
        "like (11025 Hz Nyquist is 5.5125 kHz)."
    ),
    "hf_ratio_4k_nyquist_db": (
        "Same but 4000 Hz to each file's own Nyquist; informational, reported "
        "for the reference set only (for the 11025 Hz group it is ~the capped "
        "value)."
    ),
    "snr_proxy_db": (
        "20*log10(P90/P10) of non-overlapping 25 ms frame RMS; a rough "
        "noise-floor indicator (higher is better)."
    ),
    "consonant_proxy_db": (
        "10*log10(mean PSD in 2000-5000 Hz / mean PSD in 300-2000 Hz); a "
        "fricative/pharyngeal consonant-energy proxy (higher is better)."
    ),
}

DIST_KEYS = ["min", "p05", "p25", "median", "p75", "p95", "max", "mean"]


def band_energy(freqs: np.ndarray, psd: np.ndarray, lo: float, hi: float) -> float:
    mask = (freqs >= lo) & (freqs < hi)
    return float(psd[mask].sum())


def band_mean(freqs: np.ndarray, psd: np.ndarray, lo: float, hi: float) -> float:
    mask = (freqs >= lo) & (freqs < hi)
    if not mask.any():
        return 0.0
    return float(psd[mask].mean())


def db_ratio(num: float, den: float, floor_db: float = -200.0) -> float:
    if den <= 0 or num <= 0:
        return floor_db
    return float(10.0 * np.log10(num / den))


def compute_metrics(mono: np.ndarray, sr: int) -> dict:
    mono = np.asarray(mono, dtype=np.float64).ravel()
    nper = min(2048, len(mono))
    freqs, psd = welch(mono, fs=sr, window="hann", nperseg=nper,
                       noverlap=nper // 2, scaling="density")
    nyquist = sr / 2.0

    n_top = max(1, int(np.ceil(NOISE_TOP_FRACTION * len(psd))))
    noise_floor_power = float(np.median(psd[-n_top:]))
    if noise_floor_power <= 0:
        noise_floor_power = float(np.finfo(float).tiny)
    threshold = noise_floor_power * (10.0 ** (CUTOFF_ABOVE_DB / 10.0))
    above = np.flatnonzero(psd > threshold)
    cutoff_hz = float(freqs[above[-1]]) if above.size else 0.0

    total = float(psd.sum())
    hf_capped = band_energy(freqs, psd, *HF_BAND)
    hf_full = band_energy(freqs, psd, HF_BAND[0], nyquist + 1.0)

    frame = max(1, int(round(FRAME_S * sr)))
    n_frames = len(mono) // frame
    if n_frames >= 2:
        frames = mono[: n_frames * frame].reshape(n_frames, frame)
        rms = np.sqrt(np.mean(frames ** 2, axis=1))
        p10, p90 = np.percentile(rms, 10), np.percentile(rms, 90)
        snr_db = 20.0 * np.log10(p90 / p10) if p10 > 0 else 200.0
    else:
        snr_db = 0.0

    cons = band_mean(freqs, psd, *CONSONANT_BAND)
    low = band_mean(freqs, psd, *LOW_BAND)

    return {
        "sample_rate": int(sr),
        "duration_s": round(len(mono) / sr, 6),
        "bandwidth_cutoff_hz": round(cutoff_hz, 2),
        "noise_floor_psd": noise_floor_power,
        "hf_ratio_4k_5p5k_db": round(db_ratio(hf_capped, total), 4),
        "hf_ratio_4k_nyquist_db": round(db_ratio(hf_full, total), 4),
        "snr_proxy_db": round(float(snr_db), 4),
        "consonant_proxy_db": round(db_ratio(cons, low), 4),
    }


def distribution(values: list[float]) -> dict:
    a = np.asarray(values, dtype=float)
    return {
        "min": round(float(a.min()), 4),
        "p05": round(float(np.percentile(a, 5)), 4),
        "p25": round(float(np.percentile(a, 25)), 4),
        "median": round(float(np.percentile(a, 50)), 4),
        "p75": round(float(np.percentile(a, 75)), 4),
        "p95": round(float(np.percentile(a, 95)), 4),
        "max": round(float(a.max()), 4),
        "mean": round(float(a.mean()), 4),
        "n": int(a.size),
    }


def percentile_rank(value: float, sorted_ref: list[float]) -> float:
    """Percent of reference values <= value (0-100)."""
    return round(100.0 * bisect.bisect_right(sorted_ref, value) / len(sorted_ref), 4)


METRICS_FOR_RULE = ["bandwidth_cutoff_hz", "hf_ratio_4k_5p5k_db",
                    "snr_proxy_db", "consonant_proxy_db"]


def classify(metrics: dict, ref_p05: dict, ref_p95: dict) -> dict:
    """Apply the fixed rule; returns verdict + which metrics drove it."""
    below = {m: bool(metrics[m] < ref_p05[m]) for m in METRICS_FOR_RULE}
    inside = {m: bool(ref_p05[m] <= metrics[m] <= ref_p95[m])
              for m in METRICS_FOR_RULE}

    consonant_worse = below["hf_ratio_4k_5p5k_db"] or below["consonant_proxy_db"]
    other_worse = below["bandwidth_cutoff_hz"] or below["snr_proxy_db"]
    if consonant_worse and other_worse:
        driving = [m for m in METRICS_FOR_RULE if below[m]]
        return {"verdict": "DISQUALIFY", "driving_metrics": driving,
                "below_p05": below, "inside_5_95": inside}
    if inside["hf_ratio_4k_5p5k_db"] and inside["consonant_proxy_db"]:
        return {"verdict": "KEEP", "driving_metrics": [], "below_p05": below,
                "inside_5_95": inside}
    driving = [m for m in METRICS_FOR_RULE
               if not inside[m] or below[m]]
    return {"verdict": "INCONCLUSIVE", "driving_metrics": driving,
            "below_p05": below, "inside_5_95": inside}


def md5(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _ffprobe_format(path: str) -> dict:
    import subprocess

    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=sample_rate,channels,bit_rate",
         "-of", "json", path], capture_output=True, text=True, check=True,
    )
    s = json.loads(out.stdout)["streams"][0]
    return {"sample_rate": int(s["sample_rate"]),
            "channels": int(s["channels"]),
            "bit_rate": int(s.get("bit_rate") or 0)}


def _load_metrics(path: str) -> dict:
    waveform, sr = torchaudio.load(path)
    mono = waveform.mean(dim=0).numpy() if waveform.shape[0] > 1 else waveform[0].numpy()
    return compute_metrics(mono, int(sr))


def load_md5_map(repo: str) -> dict:
    path = os.path.join(repo, "data/pron/integrity_check.json")
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    return {f"{f['reciter']}/{f['key']}": f["local_md5"] for f in doc["files"]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--audio-dir", default="data/pron/donor_audio")
    ap.add_argument("--out", default="data/pron/abu_bakr_lowq_verification.json")
    ap.add_argument("--workers", type=int, default=min(8, os.cpu_count() or 1))
    args = ap.parse_args(argv)

    folder = os.path.join(args.audio_dir, RECITER)
    names = sorted(n for n in os.listdir(folder) if n.endswith(".mp3"))
    entries = []
    with ThreadPoolExecutor(max_workers=16) as pool:
        for name, fmt in zip(names, pool.map(
                lambda n: _ffprobe_format(os.path.join(folder, n)), names)):
            entries.append({"key": name[:-4], "format": fmt})

    low = [e for e in entries if e["format"]["sample_rate"] == LOW_SAMPLE_RATE]
    ref = [e for e in entries if e["format"]["sample_rate"] != LOW_SAMPLE_RATE]
    low_keys = sorted(e["key"] for e in low)

    def work(entry):
        path = os.path.join(folder, f"{entry['key']}.mp3")
        metrics = _load_metrics(path)
        metrics["key"] = entry["key"]
        metrics["format"] = entry["format"]
        metrics["is_low_format"] = entry["format"]["sample_rate"] == LOW_SAMPLE_RATE
        return metrics

    all_entries = low + ref
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(work, all_entries))

    ref_metrics = [r for r in results if not r["is_low_format"]]
    low_metrics = [r for r in results if r["is_low_format"]]
    ref_dist = {m: distribution([r[m] for r in ref_metrics])
                for m in ["bandwidth_cutoff_hz", "hf_ratio_4k_5p5k_db",
                          "hf_ratio_4k_nyquist_db", "snr_proxy_db",
                          "consonant_proxy_db"]}
    ref_sorted = {m: sorted(r[m] for r in ref_metrics)
                  for m in METRICS_FOR_RULE}
    ref_p05 = {m: ref_dist[m]["p05"] for m in METRICS_FOR_RULE}
    ref_p95 = {m: ref_dist[m]["p95"] for m in METRICS_FOR_RULE}

    md5_map = load_md5_map(os.getcwd())
    files_out = []
    counts = {"DISQUALIFY": 0, "KEEP": 0, "INCONCLUSIVE": 0}
    proposed_exclusions = []
    for r in sorted(low_metrics, key=lambda x: x["key"]):
        cls = classify(r, ref_p05, ref_p95)
        counts[cls["verdict"]] += 1
        rec = {
            "key": r["key"],
            "format": r["format"],
            "metrics": {m: r[m] for m in
                        ["bandwidth_cutoff_hz", "hf_ratio_4k_5p5k_db",
                         "hf_ratio_4k_nyquist_db", "snr_proxy_db",
                         "consonant_proxy_db"]},
            "percentile_rank": {m: percentile_rank(r[m], ref_sorted[m])
                                for m in METRICS_FOR_RULE},
            "below_p05": cls["below_p05"],
            "inside_5_95": cls["inside_5_95"],
            "verdict": cls["verdict"],
            "driving_metrics": cls["driving_metrics"],
        }
        if cls["verdict"] == "DISQUALIFY":
            proposed_exclusions.append({
                "reciter": RECITER, "key": r["key"],
                "reason": (
                    "verified low quality: 11025Hz/mono/24kbps and below "
                    "reference 5th percentile on "
                    + ", ".join(cls["driving_metrics"]) + " (Task 12)"
                ),
            })
        files_out.append(rec)

    md5_check = {}
    for r in low_metrics:
        path = os.path.join(folder, f"{r['key']}.mp3")
        current = md5(path)
        recorded = md5_map.get(f"{RECITER}/{r['key']}")
        md5_check[r["key"]] = {
            "md5": current,
            "recorded_md5": recorded,
            "matches_recorded": recorded is not None and current == recorded,
            "recorded_source": "data/pron/integrity_check.json" if recorded
            else None,
        }

    fmt_counter = {}
    for e in ref:
        sig = f"{e['format']['sample_rate']}Hz/{e['format']['channels']}ch/{e['format']['bit_rate']}bps"
        fmt_counter[sig] = fmt_counter.get(sig, 0) + 1

    doc = {
        "task": "Task 12 - verify the 14 low-format Abu_Bakr donor files",
        "reciter": RECITER,
        "read_only": True,
        "decoder": "torchaudio.load (the training loader's decoder), mixed to mono, native sample rate",
        "question": (
            "Are the 14 11025Hz/mono/24kbps files actually low quality enough "
            "to damage the crisp-consonant signal (ح خ ع ض)?"
        ),
        "low_format_group": {
            "definition": "sample_rate == 11025 (selected by ffprobe over all 350 files in the folder; not by manifest key)",
            "count": len(low),
            "keys": low_keys,
            "formats": {
                f"{low[0]['format']['sample_rate']}Hz/"
                f"{low[0]['format']['channels']}ch/"
                f"{low[0]['format']['bit_rate']}bps": len(low)
            },
        },
        "reference_group": {
            "definition": "all other files in the folder (sample_rate != 11025)",
            "count": len(ref),
            "formats": fmt_counter,
        },
        "metric_definitions": METRIC_DEFINITIONS,
        "rule": RULE_TEXT,
        "reference_distribution": ref_dist,
        "files": files_out,
        "verdict_summary": counts,
        "proposed_exclusions": proposed_exclusions,
        "md5_check": md5_check,
        "concerns": (
            "No thresholds were tuned after seeing results. Note the fixed rule "
            "compares each metric against the reference 5th percentile; the "
            "bandwidth_cutoff_hz metric is bounded by Nyquist (5.5 kHz for the "
            "low group), so it can only register as 'below' the reference if "
            "clean files' cutoffs are also low. This is reported as-is, per the "
            "task; any threshold change is left to the user."
        ),
    }
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2)

    print(f"low-format files : {len(low)} ({', '.join(low_keys)})")
    print(f"reference files  : {len(ref)}")
    print("reference distribution (used for percentiles):")
    for m in ["bandwidth_cutoff_hz", "hf_ratio_4k_5p5k_db",
              "hf_ratio_4k_nyquist_db", "snr_proxy_db", "consonant_proxy_db"]:
        d = ref_dist[m]
        print(f"  {m:<24} min={d['min']} p05={d['p05']} med={d['median']} "
              f"p95={d['p95']} max={d['max']}")
    print("per-file:")
    for r in files_out:
        print(f"  {r['key']}  {r['verdict']:<12} "
              f"cut={r['metrics']['bandwidth_cutoff_hz']} "
              f"hf={r['metrics']['hf_ratio_4k_5p5k_db']} "
              f"snr={r['metrics']['snr_proxy_db']} "
              f"cons={r['metrics']['consonant_proxy_db']} "
              f"drivers={r['driving_metrics']}")
    print(f"summary: {counts}")
    bad = [k for k, v in md5_check.items() if not v["matches_recorded"]]
    print(f"md5 verified: {len(md5_check) - len(bad)}/{len(md5_check)}"
          + (f" MISMATCH {bad}" if bad else ""))
    print(f"\nwritten: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
