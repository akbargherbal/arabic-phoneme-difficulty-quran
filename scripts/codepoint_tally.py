#!/usr/bin/env python3
"""Tally occurrences of codepoints U+06D6..U+06ED in the corpus editions.

Helps decide which signs are actually present and whether the undecided
rounded/empty-centre high stops (U+06EA..U+06EC) occur at all.  Prints one row
per codepoint that occurs, plus an example ayah for each undecided codepoint.

Usage:
    python scripts/codepoint_tally.py
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aya_scoring import arabic  # noqa: E402

RANGE = range(0x06D6, 0x06EE)  # U+06D6..U+06ED inclusive


def classify(cp: int) -> str:
    if cp in arabic.PAUSE_SIGN_CODEPOINTS:
        return "pause(default-strip)"
    if cp in arabic.NON_PRONUNCIATION_CODEPOINTS:
        return "annotation(opt-in)"
    if cp in arabic.UNDECIDED_HIGH_STOP_CODEPOINTS:
        return "UNDECIDED(keep)"
    return "protected(keep)"


def load_verses(path: str) -> list[tuple[str, str, str]]:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    verses = data["verses"] if isinstance(data, dict) else data
    return [
        (f"{int(v['surah']):03d}{int(v['ayah']):03d}", v["text"], v["text"])
        for v in verses
    ]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--uthmani", default="data/quran/uthmani.json")
    ap.add_argument("--simple", default="data/quran/simple.json")
    args = ap.parse_args(argv)

    editions = {
        "uthmani": load_verses(args.uthmani),
        "simple": load_verses(args.simple),
    }

    counts: dict[str, dict[int, int]] = {}
    examples: dict[str, dict[int, tuple[str, str]]] = {}
    for name, verses in editions.items():
        counts[name] = {cp: 0 for cp in RANGE}
        examples[name] = {}
        for key, text, _ in verses:
            for ch in text:
                cp = ord(ch)
                if cp in counts[name]:
                    counts[name][cp] += 1
                    examples[name].setdefault(cp, (key, text))

    header = (
        f"{'codepoint':<10} {'char':<4} {'category':<20} "
        f"{'uthmani':>8} {'simple':>8}  name"
    )
    print(header)
    print("-" * len(header))
    for cp in RANGE:
        u, s = counts["uthmani"][cp], counts["simple"][cp]
        if u == 0 and s == 0:
            continue
        ch = chr(cp)
        print(
            f"U+{cp:04X}     {ch:<4} {classify(cp):<20} {u:>8} {s:>8}  "
            f"{unicodedata.name(ch, '?')}"
        )

    print("\nUndecided high stops in range U+06EA..U+06EC:")
    for cp in arabic.UNDECIDED_HIGH_STOP_CODEPOINTS:
        for name in ("uthmani", "simple"):
            u = counts[name][cp]
            if u:
                key, text = examples[name][cp]
                print(f"  U+{cp:04X} ({chr(cp)}) {name}: {u} occurrence(s); "
                      f"e.g. {key}: {text}")
            else:
                print(f"  U+{cp:04X} ({chr(cp)}) {name}: 0 occurrences")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
