#!/usr/bin/env python3
"""Build the human listening-review zip from the flagged donor files.

Reads the Task 8 report and stages, for direct human review only:

* every ``silence_long_gap`` file (all of them), and
* a seeded, per-reciter-proportional sample of ``loudness_outlier`` files.

Source mp3 bytes are copied as-is into the zip - never trimmed, re-encoded or
gain-adjusted.  The zip lives outside the repo and is deliberately not
committed.

Usage:
    python scripts/pron_human_review_zip.py \
        --report data/pron/audio_level_silence_report.json \
        --audio-dir data/pron/donor_audio \
        --out /content/human_review.zip
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import shutil
import zipfile

SEED = 42
LOUDNESS_TARGET = 30
LOUDNESS_CAP = 4
LOUDNESS_MIN = 1


def allocate_quotas(
    counts: dict[str, int], target: int, cap: int = LOUDNESS_CAP,
    minimum: int = LOUDNESS_MIN,
) -> dict[str, int]:
    """Proportional (D'Hondt) sample allocation with a per-reciter cap.

    Every reciter with >= 1 flagged file gets at least ``minimum``; no reciter
    gets more than ``min(cap, its flag count)``; the quotas sum to ``target``
    when feasible.
    """
    eligible = {r: c for r, c in counts.items() if c > 0}
    caps = {r: min(cap, c) for r, c in eligible.items()}
    quotas = {r: min(minimum, caps[r]) for r in eligible}
    if not eligible:
        return quotas

    def total() -> int:
        return sum(quotas.values())

    while total() < target:
        under = [r for r in eligible if quotas[r] < caps[r]]
        if not under:
            break
        pick = max(under, key=lambda r: (eligible[r] / (quotas[r] + 1),
                                         eligible[r], r))
        quotas[pick] += 1
    while total() > target:
        over = [r for r in eligible if quotas[r] > min(minimum, caps[r])]
        if not over:
            break
        pick = min(over, key=lambda r: (eligible[r] / quotas[r], r))
        quotas[pick] -= 1
    return quotas


def md5(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--report", default="data/pron/audio_level_silence_report.json")
    ap.add_argument("--audio-dir", default="data/pron/donor_audio")
    ap.add_argument("--out", default="/content/human_review.zip")
    args = ap.parse_args(argv)

    with open(args.report, encoding="utf-8") as fh:
        report = json.load(fh)
    reciters = report["reciters"]
    files = {(f["reciter"], f["key"]): f for f in report["files"]}

    silence = [f for f in report["files"] if "silence_long_gap" in f["flags"]]
    loudness = [f for f in report["files"] if "loudness_outlier" in f["flags"]]
    silence.sort(key=lambda f: (f["reciter"], f["key"]))
    loudness.sort(key=lambda f: (f["reciter"], f["key"]))

    counts = {r: 0 for r in reciters}
    for f in loudness:
        counts[f["reciter"]] += 1
    quotas = allocate_quotas(counts, LOUDNESS_TARGET)

    random.seed(SEED)
    sampled: list[dict] = []
    for r in reciters:
        pool = sorted(f["key"] for f in loudness if f["reciter"] == r)
        if not pool or quotas.get(r, 0) <= 0:
            continue
        for k in random.sample(pool, quotas[r]):
            sampled.append(files[(r, k)])

    stage = "/tmp/opencode/task9_review"
    if os.path.isdir(stage):
        shutil.rmtree(stage)
    os.makedirs(os.path.join(stage, "silence_long_gap"))
    os.makedirs(os.path.join(stage, "loudness_outlier_sample"))

    lines = ["Human listening review - donor audio quality flags (Task 9)", ""]
    lines.append("Nothing here is modified: original mp3 bytes, copied as-is. "
                 "No trimming, no re-encoding, no gain adjustment.")
    lines.append("")

    lines.append(f"silence_long_gap/ ({len(silence)} files) - listen for the "
                 "long single silent stretch and judge if it is natural:")
    for f in silence:
        base = f"{f['reciter']}__{f['key']}.mp3"
        src = os.path.join(args.audio_dir, f["reciter"], f"{f['key']}.mp3")
        shutil.copy2(src, os.path.join(stage, "silence_long_gap", base))
        pos = f["silent_run_position"]
        lines.append(
            f"  {base} - silent gap of {f['longest_silent_run_s']}s at "
            f"{pos['start_s']}-{pos['end_s']}s (of {f['duration_s']}s total)"
        )
    lines.append("")

    lines.append(f"loudness_outlier_sample/ ({len(sampled)} files) - listen for "
                 "a clearly audible level difference vs the same reciter:")
    for f in sampled:
        base = f"{f['reciter']}__{f['key']}.mp3"
        src = os.path.join(args.audio_dir, f["reciter"], f"{f['key']}.mp3")
        shutil.copy2(src, os.path.join(stage, "loudness_outlier_sample", base))
        lines.append(
            f"  {base} - {f['deviation_db']:+.1f} dB vs this reciter's own "
            f"median ({f['lufs']} LUFS vs {f['reciter_median_lufs']} LUFS "
            "median)"
        )
    lines.append("")
    lines.append("Per-reciter loudness sample counts:")
    for r in reciters:
        lines.append(f"  {r}: {quotas.get(r, 0)} of {counts[r]} flagged")

    with open(os.path.join(stage, "MANIFEST.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")

    # Verify every staged copy is byte-identical to its source.
    verified = 0
    for sub in ("silence_long_gap", "loudness_outlier_sample"):
        for name in os.listdir(os.path.join(stage, sub)):
            reciter, key = name[:-4].split("__", 1)
            src = os.path.join(args.audio_dir, reciter, f"{key}.mp3")
            assert md5(src) == md5(os.path.join(stage, sub, name)), name
            verified += 1

    if os.path.exists(args.out):
        os.remove(args.out)
    with zipfile.ZipFile(args.out, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, names in os.walk(stage):
            for name in sorted(names):
                full = os.path.join(root, name)
                zf.write(full, os.path.relpath(full, stage))

    print(f"silence files      : {len(silence)}")
    print(f"loudness sampled   : {len(sampled)} (target {LOUDNESS_TARGET}, "
          f"cap {LOUDNESS_CAP})")
    print("per-reciter loudness quotas:")
    for r in reciters:
        print(f"  {r:<32} {quotas.get(r, 0)} of {counts[r]}")
    print(f"bytes copied verified: {verified}/{len(silence) + len(sampled)}")
    print(f"zip                : {args.out}")
    print(f"zip size           : {os.path.getsize(args.out)} bytes")
    with zipfile.ZipFile(args.out) as zf:
        members = zf.namelist()
        print(f"zip entries        : {len(members)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
