#!/usr/bin/env python3
"""Download the Quran text from Tanzil in several scripts.

Tanzil (https://tanzil.net) publishes a fully verified, diacritized Quran text
in a number of scripts (Uthmani, Imla'ei "simple", plain/minimal variants).
This script fetches every variant, strips the basmala that Tanzil prepends to
the first ayah of each surah, and normalises everything into:

    data/raw/tanzil/<script>.txt     verbatim download (incl. licence header)
    data/quran/<script>.json         [{surah, ayah, text}, ...]
    data/quran/<script>.csv
    data/quran/manifest.json         provenance + counts

Usage:
    python download_corpus.py                     # all scripts
    python download_corpus.py --scripts uthmani simple
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone

import requests

from aya_scoring.arabic import base_skeleton, words

TANZIL_URL = (
    "https://tanzil.net/pub/download/index.php"
    "?quranType={script}&outType=txt-2&agree=true&marks=true&sajdah=true&tatweel=true"
)

# script key -> human description
SCRIPTS: dict[str, str] = {
    "uthmani": "Uthmani text as in the Madina Mushaf (primary source)",
    "uthmani-min": "Uthmani text with a minimal number of diacritics/symbols",
    "simple": "Simple (Imla'ei) text, fully diacritized",
    "simple-plain": "Simple text without ikhfa/idgham demonstration",
    "simple-min": "Simple text with minimal diacritics",
    "simple-clean": "Simple text without any diacritics (skeleton only)",
}

BASMALA = ["بسم", "الله", "الرحمن", "الرحيم"]
BASMALA_SKELETON = "".join(base_skeleton(w) for w in BASMALA)

RAW_DIR = os.path.join("data", "raw", "tanzil")
OUT_DIR = os.path.join("data", "quran")


def download(script: str) -> str:
    url = TANZIL_URL.format(script=script)
    resp = requests.get(url, timeout=120)
    resp.raise_for_status()
    if len(resp.text) < 1000 or "File not found" in resp.text:
        raise RuntimeError(f"unexpected response for {script!r}")
    return resp.text


def parse_verses(raw: str) -> list[dict]:
    verses: list[dict] = []
    for line in raw.splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split("|", 2)
        if len(parts) != 3:
            continue
        surah, ayah, text = parts
        if not surah.isdigit() or not ayah.isdigit():
            continue
        verses.append({"surah": int(surah), "ayah": int(ayah), "text": text.strip()})
    return verses


def strip_basmala(verses: list[dict]) -> tuple[list[dict], int]:
    """Remove the basmala Tanzil prepends to first ayat (except 1 & 9)."""
    out: list[dict] = []
    stripped = 0
    for v in verses:
        text = v["text"]
        if v["ayah"] == 1 and v["surah"] not in (1, 9):
            toks = words(text)
            if len(toks) > 4:
                prefix = "".join(base_skeleton(t) for t in toks[:4])
                if prefix == BASMALA_SKELETON:
                    text = " ".join(toks[4:])
                    stripped += 1
        out.append({**v, "text": text})
    return out, stripped


def write_corpus(script: str, raw: str, verses: list[dict]) -> dict:
    os.makedirs(RAW_DIR, exist_ok=True)
    os.makedirs(OUT_DIR, exist_ok=True)
    raw_path = os.path.join(RAW_DIR, f"{script}.txt")
    with open(raw_path, "w", encoding="utf-8") as fh:
        fh.write(raw)

    payload = {
        "script": script,
        "description": SCRIPTS[script],
        "source": "Tanzil Project (https://tanzil.net)",
        "license": "Creative Commons Attribution 3.0 (see raw file header)",
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
        "basmala_stripped": True,
        "verse_count": len(verses),
        "verses": verses,
    }
    json_path = os.path.join(OUT_DIR, f"{script}.json")
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)

    csv_path = os.path.join(OUT_DIR, f"{script}.csv")
    with open(csv_path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["surah", "ayah", "text"])
        writer.writeheader()
        writer.writerows(verses)

    return {
        "script": script,
        "description": SCRIPTS[script],
        "raw": raw_path,
        "json": json_path,
        "csv": csv_path,
        "verse_count": len(verses),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--scripts", nargs="*", default=list(SCRIPTS),
                    choices=list(SCRIPTS))
    args = ap.parse_args(argv)

    manifest = {
        "source": "Tanzil Project",
        "url": "https://tanzil.net",
        "license": "Creative Commons Attribution 3.0",
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
        "scripts": {},
    }
    for script in args.scripts:
        print(f"downloading {script} ...", flush=True)
        raw = download(script)
        verses = parse_verses(raw)
        if len(verses) != 6236:
            print(f"  [warn] expected 6236 verses, parsed {len(verses)}", file=sys.stderr)
        verses, stripped = strip_basmala(verses)
        info = write_corpus(script, raw, verses)
        info["basmala_stripped"] = stripped
        manifest["scripts"][script] = info
        print(f"  {len(verses)} verses, basmala stripped from {stripped} first-ayat")

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
    print(f"manifest -> {os.path.join(OUT_DIR, 'manifest.json')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
