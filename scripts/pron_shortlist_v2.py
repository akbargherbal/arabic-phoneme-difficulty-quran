#!/usr/bin/env python3
"""Build the v2 pronunciation shortlist (type A shatr / type B bayt donors).

Re-cuts the density-ranked scoring output into:
  * type A: 4-6 word ayat  (~ one shatr)
  * type B: 7-12 word ayat (~ one bayt)
~25 type A and ~15 type B are selected, each ranked by density.

Hard excludes any ayah containing U+06EA/U+06EB/U+06EC (special-reading marks,
non-standard pronunciation).  If any target letter (ح خ ع ض) ends up in fewer
than ``--min-letter-coverage`` of the selected ayat, a coverage-floor local
search swaps in higher-density ayat for that letter (type counts preserved) and
the method is reported as ``balanced-per-letter``.

Usage:
    python scripts/pron_shortlist_v2.py \
        --scores data/pron/v2/ayah_scores.csv \
        --out data/pron/pron_shortlist_v2.csv
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
# Special-reading marks: never pronounce these as standard Hafs.
EXCLUDE_CODEPOINTS = {0x06EA, 0x06EB, 0x06EC}
TYPE_A_RANGE = (4, 6)
TYPE_B_RANGE = (7, 12)


def ayah_key(surah: int, ayah: int) -> str:
    return f"{surah:03d}{ayah:03d}"


def count_targets(text: str) -> dict[str, int]:
    letters = arabic.base_letters(text)
    return {letter: letters.count(letter) for letter in TARGET_LETTERS}


def load_rows(path: str) -> tuple[list[dict], list[str]]:
    with open(path, encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    out: list[dict] = []
    excluded: list[str] = []
    for row in rows:
        row["surah"] = int(row["surah"])
        row["ayah"] = int(row["ayah"])
        row["word_count"] = int(row["word_count"])
        row["raw_score"] = float(row["raw_score"])
        row["density_score"] = float(row["density_score"])
        row["key"] = ayah_key(row["surah"], row["ayah"])
        if any(ord(ch) in EXCLUDE_CODEPOINTS for ch in row["text"]):
            excluded.append(row["key"])
            continue
        row["_counts"] = count_targets(row["text"])
        out.append(row)
    return out, excluded


def coverage(rows: list[dict]) -> dict[str, int]:
    return {
        letter: sum(1 for row in rows if row["_counts"][letter] > 0)
        for letter in TARGET_LETTERS
    }


def min_coverage(rows: list[dict]) -> int:
    return min(coverage(rows).values())


def balanced_pick(a_pool: list[dict], b_pool: list[dict],
                  a_n: int, b_n: int, floor: int) -> list[dict]:
    """Local search preserving type counts and lifting per-letter coverage.

    Only used when the plain density pick misses the coverage floor.  Swaps one
    selected ayah for an unselected one of the same type, always choosing the
    swap that most improves ``sum(min(count(letter), floor))``.
    """
    selected = a_pool[:a_n] + b_pool[:b_n]

    def score(rows: list[dict]) -> int:
        cov = coverage(rows)
        return sum(min(cov[letter], floor) for letter in TARGET_LETTERS)

    current = score(selected)
    while True:
        chosen = {row["key"] for row in selected}
        best: list[dict] | None = None
        best_score = current
        for letter in TARGET_LETTERS:
            if coverage(selected)[letter] >= floor:
                continue
            for pool in (a_pool, b_pool):
                for cand in pool:
                    if cand["key"] in chosen or cand["_counts"][letter] == 0:
                        continue
                    victim_type = cand["length_type"]
                    for i, victim in enumerate(selected):
                        if victim["length_type"] != victim_type:
                            continue
                        trial = selected[:i] + selected[i + 1:] + [cand]
                        s = score(trial)
                        if s > best_score:
                            best_score = s
                            best = trial
        if best is None:
            break
        selected = best
        current = best_score

    selected.sort(key=lambda r: (r["density_score"], r["raw_score"]), reverse=True)
    return selected


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--scores", default="data/pron/v2/ayah_scores.csv")
    ap.add_argument("--out", default="data/pron/pron_shortlist_v2.csv")
    ap.add_argument("--type-a-count", type=int, default=25)
    ap.add_argument("--type-b-count", type=int, default=15)
    ap.add_argument("--min-letter-coverage", type=int, default=12)
    args = ap.parse_args(argv)

    rows, excluded = load_rows(args.scores)
    for row in rows:
        row["length_type"] = "A" if TYPE_A_RANGE[0] <= row["word_count"] <= TYPE_A_RANGE[1] else "B"

    a_pool = [r for r in rows if r["length_type"] == "A"]
    b_pool = [r for r in rows if r["length_type"] == "B"]

    selected = a_pool[: args.type_a_count] + b_pool[: args.type_b_count]
    method = "density-within-type"
    cov = coverage(selected)
    if min(cov.values()) < args.min_letter_coverage:
        method = "balanced-per-letter"
        selected = balanced_pick(
            a_pool, b_pool, args.type_a_count, args.type_b_count,
            args.min_letter_coverage,
        )
        cov = coverage(selected)

    selected.sort(key=lambda r: (r["length_type"], r["density_score"]),
                  reverse=True)
    selected.sort(key=lambda r: r["length_type"])

    fieldnames = [
        "key", "surah", "ayah", "word_count", "length_type", "raw_score",
        "density_score", *COUNT_COLUMNS.values(),
    ]
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in selected:
            writer.writerow({
                "key": row["key"],
                "surah": row["surah"],
                "ayah": row["ayah"],
                "word_count": row["word_count"],
                "length_type": row["length_type"],
                "raw_score": row["raw_score"],
                "density_score": row["density_score"],
                **{COUNT_COLUMNS[letter]: row["_counts"][letter]
                   for letter in TARGET_LETTERS},
            })

    wc: dict[int, int] = {}
    for row in selected:
        wc[row["word_count"]] = wc.get(row["word_count"], 0) + 1
    print(f"method          : {method}")
    print(f"selected        : {len(selected)} "
          f"(A={sum(1 for r in selected if r['length_type'] == 'A')}, "
          f"B={sum(1 for r in selected if r['length_type'] == 'B')})")
    print(f"word-count dist : {dict(sorted(wc.items()))}")
    print(f"letter coverage : {cov}")
    print(f"excluded marks  : {excluded}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
