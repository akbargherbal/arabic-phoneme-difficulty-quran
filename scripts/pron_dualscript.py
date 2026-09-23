#!/usr/bin/env python3
"""Join the pronunciation shortlist against both corpus editions, per ayah.

For every key in the shortlist CSV, emit one JSONL record carrying the Uthmani
text and the Tanzil "simple" (Imla'ei) text, both raw (untouched, diacritics and
marks preserved) and pause-clean (small-high waqf signs removed with
:func:`aya_scoring.arabic.strip_pause_marks`).  Raw fields stay in the record so
the pause signs can be revisited later.

Ayat are matched 1:1 by ``(surah, ayah)``.  Any shortlist key that cannot be
matched in either edition is reported and skipped (never silently dropped);
Uthmani-vs-simple word-count differences are reported and flagged in-record.

Usage:
    python scripts/pron_dualscript.py \
        --shortlist data/pron/pron_shortlist.csv \
        --out data/pron_shortlist_dualscript.jsonl
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aya_scoring import arabic  # noqa: E402

EXPECTED_VERSE_COUNT = 6236


def ayah_key(surah: int, ayah: int) -> str:
    return f"{surah:03d}{ayah:03d}"


def load_edition(path: str) -> tuple[dict[tuple[int, int], str], int]:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    verses = data["verses"] if isinstance(data, dict) else data
    return {(int(v["surah"]), int(v["ayah"])): v["text"] for v in verses}, len(verses)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--shortlist", default="data/pron/pron_shortlist_v2.csv")
    ap.add_argument("--uthmani", default="data/quran/uthmani.json")
    ap.add_argument("--simple", default="data/quran/simple.json")
    ap.add_argument("--out", default="data/pron_shortlist_dualscript.jsonl")
    args = ap.parse_args(argv)

    uthmani, n_uthmani = load_edition(args.uthmani)
    simple, n_simple = load_edition(args.simple)
    for name, count in (("uthmani", n_uthmani), ("simple", n_simple)):
        if count != EXPECTED_VERSE_COUNT:
            print(f"[warn] {name}: {count} verses (expected {EXPECTED_VERSE_COUNT})",
                  file=sys.stderr)

    with open(args.shortlist, encoding="utf-8", newline="") as fh:
        shortlist = list(csv.DictReader(fh))

    records = []
    unmatched: list[str] = []
    wc_mismatches: list[dict] = []
    for row in shortlist:
        surah, ayah = int(row["surah"]), int(row["ayah"])
        key = row.get("key") or ayah_key(surah, ayah)
        if (surah, ayah) not in uthmani or (surah, ayah) not in simple:
            unmatched.append(key)
            continue
        u_raw = uthmani[(surah, ayah)]
        s_raw = simple[(surah, ayah)]
        wc_u = len(u_raw.split())
        wc_s = len(s_raw.split())
        record = {
            "key": key,
            "surah": surah,
            "ayah": ayah,
            "length_type": row.get("length_type") or None,
            "uthmani_raw": u_raw,
            "uthmani_clean": arabic.strip_pause_marks(u_raw),
            "simple_raw": s_raw,
            "simple_clean": arabic.strip_pause_marks(s_raw),
            "word_count_uthmani": wc_u,
            "word_count_simple": wc_s,
            "word_count_differs": wc_u != wc_s,
        }
        records.append(record)
        if wc_u != wc_s:
            wc_mismatches.append({"key": key, "uthmani": wc_u, "simple": wc_s})

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    print(f"records written : {len(records)} -> {args.out}")
    print(f"unmatched keys  : {unmatched if unmatched else 'none'}")
    print(f"word-count diffs: {len(wc_mismatches)}")
    for m in wc_mismatches:
        print(f"  {m['key']}: uthmani={m['uthmani']} simple={m['simple']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
