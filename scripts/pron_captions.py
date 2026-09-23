#!/usr/bin/env python3
"""Generate one caption .txt per shortlisted key and script.

Layout (exactly, with a single final newline)::

    Solo male voice, unaccompanied. Clear precise Arabic diction, measured pace.
    [Lyrics]
    [Verse]
    <uthmani_clean | simple_clean>

The clean text is the pause-stripped text from the dual-script JSONL; every
other Uthmani/pronunciation mark is left untouched.  No trigger word, ellipsis,
melisma/vibrato wording or Intro/Outro section is added.

Usage:
    python scripts/pron_captions.py \
        --dualscript data/pron_shortlist_dualscript.jsonl \
        --out-dir data/pron/captions
"""

from __future__ import annotations

import argparse
import json
import os

PREFIX = (
    "Solo male voice, unaccompanied. Clear precise Arabic diction, measured pace."
)
SCRIPTS = ("uthmani", "simple")


def caption_text(clean: str) -> str:
    return f"{PREFIX}\n[Lyrics]\n[Verse]\n{clean}\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dualscript", default="data/pron_shortlist_dualscript.jsonl")
    ap.add_argument("--out-dir", default="data/pron/captions")
    args = ap.parse_args(argv)

    with open(args.dualscript, encoding="utf-8") as fh:
        records = [json.loads(line) for line in fh if line.strip()]

    os.makedirs(args.out_dir, exist_ok=True)
    written = 0
    for record in records:
        for script in SCRIPTS:
            path = os.path.join(args.out_dir, f"{record['key']}_{script}.txt")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(caption_text(record[f"{script}_clean"]))
            written += 1

    print(f"records    : {len(records)}")
    print(f"captions   : {written} -> {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
