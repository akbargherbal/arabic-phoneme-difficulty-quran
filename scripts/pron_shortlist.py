#!/usr/bin/env python3
"""Build the ح/خ/ع/ض pronunciation-LoRA shortlist from a scored-ayah CSV.

Reads the full density-ranked table produced by ``python -m aya_scoring`` with
``configs/pron_hkhad.json``, adds per-letter counts for the four target letters,
and writes a shortlist CSV.  The default strategy is the top-N by density; if
one target letter is badly under-represented (appears in fewer than half the
quota it would get under an even split) a per-letter-balanced pick is used
instead.  The method actually used is always reported on stdout.

Usage:
    python scripts/pron_shortlist.py \
        --scores data/pron/ayah_scores.csv \
        --out data/pron/pron_shortlist.csv --top 60
"""

from __future__ import annotations

import argparse
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aya_scoring import arabic  # noqa: E402

TARGET_LETTERS = ["ح", "خ", "ع", "ض"]
COUNT_COLUMNS = {letter: f"count_{letter}" for letter in TARGET_LETTERS}


def ayah_key(surah: int, ayah: int) -> str:
    """Zero-padded SSSAAA key (e.g. 2:255 -> 002255)."""
    return f"{surah:03d}{ayah:03d}"


def count_targets(text: str) -> dict[str, int]:
    letters = arabic.base_letters(text)
    return {letter: letters.count(letter) for letter in TARGET_LETTERS}


def load_rows(path: str) -> list[dict]:
    with open(path, encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    for row in rows:
        row["surah"] = int(row["surah"])
        row["ayah"] = int(row["ayah"])
        row["word_count"] = int(row["word_count"])
        row["raw_score"] = float(row["raw_score"])
        row["density_score"] = float(row["density_score"])
        row["key"] = ayah_key(row["surah"], row["ayah"])
        row["_counts"] = count_targets(row["text"])
    return rows


def coverage(rows: list[dict]) -> dict[str, int]:
    return {
        letter: sum(1 for row in rows if row["_counts"][letter] > 0)
        for letter in TARGET_LETTERS
    }


def balanced_pick(rows: list[dict], top: int) -> tuple[list[dict], dict[str, int]]:
    """Round-robin one quota per letter, each time taking the densest new ayah.

    Quota = ``top // 4``; a letter's turn advances only when it contributes a
    row not already chosen, so heavily-overlapping letters still get their
    quota (the pool of ayat containing each letter is far larger than the
    quota).  Remaining slots are filled from the overall density order.
    """
    quota = top // len(TARGET_LETTERS)
    chosen: list[dict] = []
    chosen_keys: set[str] = set()
    for letter in TARGET_LETTERS:
        taken = 0
        for row in rows:
            if taken >= quota:
                break
            if row["key"] in chosen_keys:
                continue
            if row["_counts"][letter] > 0:
                chosen.append(row)
                chosen_keys.add(row["key"])
                taken += 1

    for row in rows:
        if len(chosen) >= top:
            break
        if row["key"] not in chosen_keys:
            chosen.append(row)
            chosen_keys.add(row["key"])

    chosen.sort(key=lambda r: (r["density_score"], r["raw_score"]), reverse=True)
    return chosen[:top], coverage(chosen[:top])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--scores", default="data/pron/ayah_scores.csv")
    ap.add_argument("--out", default="data/pron/pron_shortlist.csv")
    ap.add_argument("--top", type=int, default=60)
    ap.add_argument(
        "--min-coverage", type=float, default=0.5,
        help="Use balanced pick if any letter's coverage < this fraction of an "
             "even split (default 0.5 of top/4).",
    )
    args = ap.parse_args(argv)

    rows = load_rows(args.scores)
    if not rows:
        print("no scored rows", file=sys.stderr)
        return 1

    top_rows = rows[: args.top]
    cov = coverage(top_rows)
    quota = args.top / len(TARGET_LETTERS)
    floor = args.min_coverage * quota

    method = "top-by-density"
    if min(cov.values()) < floor:
        method = "balanced-per-letter"
        shortlist, cov = balanced_pick(rows, args.top)
    else:
        shortlist = top_rows

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    fieldnames = [
        "key", "surah", "ayah", "word_count", "raw_score", "density_score",
        *COUNT_COLUMNS.values(),
    ]
    with open(args.out, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in shortlist:
            writer.writerow({
                "key": row["key"],
                "surah": row["surah"],
                "ayah": row["ayah"],
                "word_count": row["word_count"],
                "raw_score": row["raw_score"],
                "density_score": row["density_score"],
                **{COUNT_COLUMNS[letter]: row["_counts"][letter]
                   for letter in TARGET_LETTERS},
            })

    print(f"method          : {method}")
    print(f"shortlist size  : {len(shortlist)}")
    print(f"coverage (any occurrence in an ayah): {cov}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
