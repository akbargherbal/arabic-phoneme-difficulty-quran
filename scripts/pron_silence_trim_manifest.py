#!/usr/bin/env python3
"""Compute non-destructive silence-trim offsets for the full donor corpus.

For every donor mp3 this measures the leading and trailing silence (same
-50 dBFS frame-RMS rule as ``pron_audio_quality.py``) and records only the
amount that exceeds a fixed pad, i.e. ``cut = max(0, measured - pad)``.  A file
whose edge silence is already <= the pad gets a cut of 0: it is never extended
up to the pad.  Nothing is modified or re-encoded here; the offsets are meant
to be applied later by the QA/trim step.

Usage:
    python scripts/pron_silence_trim_manifest.py \
        --audio-dir data/pron/donor_audio \
        --shortlist data/pron/pron_shortlist_v2.csv \
        --out data/pron/silence_trim_manifest.json
"""

from __future__ import annotations

import argparse
import json
import os
from concurrent.futures import ProcessPoolExecutor

from pron_audio_quality import (
    SILENCE_DBFS,
    analyze,
    load_keys,
    read_reciters,
)

DEFAULT_RECITERS_FILE = "reciters_shortlist/shortlist.txt"
DEFAULT_RECITERS_REF = "main"
PAD_S = 1.0


def excess_cut(original_s: float, pad_s: float = PAD_S) -> float:
    """Amount of edge silence to remove: the excess over the pad, never < 0."""
    return round(max(0.0, float(original_s) - pad_s), 4)


def _worker(task: tuple[str, str, str]) -> tuple[str, str, dict]:
    reciter, key, path = task
    try:
        metrics = analyze(path)
    except Exception as exc:  # noqa: BLE001 - record, never abort the run
        return reciter, key, {"error": f"{type(exc).__name__}: {exc}"}
    if "error" in metrics:
        return reciter, key, metrics
    return reciter, key, {
        "leading_silence_s": metrics["leading_silence_s"],
        "trailing_silence_s": metrics["trailing_silence_s"],
        "decode_backend": metrics.get("decode_backend"),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--audio-dir", default="data/pron/donor_audio")
    ap.add_argument("--shortlist", default="data/pron/pron_shortlist_v2.csv")
    ap.add_argument("--out", default="data/pron/silence_trim_manifest.json")
    ap.add_argument("--reciters-file", default=DEFAULT_RECITERS_FILE)
    ap.add_argument("--reciters-ref", default=DEFAULT_RECITERS_REF)
    ap.add_argument("--pad", type=float, default=PAD_S)
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

    files: list[dict] = []
    failures: list[dict] = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for reciter, key, info in pool.map(_worker, tasks, chunksize=4):
            if "error" in info:
                failures.append({"reciter": reciter, "key": key,
                                 "reason": info["error"]})
                continue
            lead = info["leading_silence_s"]
            trail = info["trailing_silence_s"]
            files.append({
                "reciter": reciter,
                "key": key,
                "leading_cut_s": excess_cut(lead, args.pad),
                "trailing_cut_s": excess_cut(trail, args.pad),
                "original_leading_s": lead,
                "original_trailing_s": trail,
            })

    files.sort(key=lambda f: (f["reciter"], f["key"]))
    per_reciter = {
        r: {"processed": 0, "nonzero_cut": 0, "leading_cut_s": 0.0,
            "trailing_cut_s": 0.0, "total_cut_s": 0.0, "failed": 0}
        for r in reciters
    }
    files_nonzero = 0
    total_cut_s = 0.0
    total_lead = 0.0
    total_trail = 0.0
    files_lead_cut = 0
    files_trail_cut = 0
    for f in files:
        rec = per_reciter[f["reciter"]]
        rec["processed"] += 1
        cut = round(f["leading_cut_s"] + f["trailing_cut_s"], 4)
        rec["leading_cut_s"] = round(rec["leading_cut_s"] + f["leading_cut_s"], 4)
        rec["trailing_cut_s"] = round(rec["trailing_cut_s"] + f["trailing_cut_s"], 4)
        rec["total_cut_s"] = round(rec["total_cut_s"] + cut, 4)
        if f["leading_cut_s"] > 0:
            files_lead_cut += 1
        if f["trailing_cut_s"] > 0:
            files_trail_cut += 1
        if cut > 0:
            files_nonzero += 1
            rec["nonzero_cut"] += 1
        total_cut_s += cut
        total_lead += f["leading_cut_s"]
        total_trail += f["trailing_cut_s"]
    for fail in failures:
        per_reciter[fail["reciter"]]["failed"] += 1

    rank = sorted(
        ((r, per_reciter[r]["nonzero_cut"]) for r in reciters),
        key=lambda kv: kv[1], reverse=True,
    )
    doc = {
        "audio_root": os.path.abspath(args.audio_dir),
        "shortlist": args.shortlist,
        "reciters": reciters,
        "reciter_count": len(reciters),
        "expected_files": len(keys) * len(reciters),
        "files_processed": len(files),
        "missing_files": missing,
        "failures": failures,
        "pad_s": args.pad,
        "silence_dbfs": SILENCE_DBFS,
        "rule": "cut = max(0, measured_edge_silence - pad_s); never pad up",
        "summary": {
            "files_with_nonzero_cut": files_nonzero,
            "files_with_leading_cut": files_lead_cut,
            "files_with_trailing_cut": files_trail_cut,
            "total_cut_s": round(total_cut_s, 4),
            "total_leading_cut_s": round(total_lead, 4),
            "total_trailing_cut_s": round(total_trail, 4),
            "per_reciter": per_reciter,
            "reciters_ranked_by_nonzero_cut": [
                {"reciter": r, "nonzero_cut": n} for r, n in rank
            ],
        },
        "files": files,
    }
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2)

    print(f"processed      : {len(files)}/{doc['expected_files']}")
    print(f"missing        : {len(missing)}")
    print(f"failures       : {len(failures)}")
    print(f"nonzero cut    : {files_nonzero}")
    print(f"total cut      : {doc['summary']['total_cut_s']} s")
    print("per reciter (nonzero_cut/processed, cut_s):")
    for r, n in rank:
        rec = per_reciter[r]
        print(f"  {r:<32} {n:>3}/{rec['processed']}  "
              f"total={rec['total_cut_s']}s")
    print(f"\nwritten: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
