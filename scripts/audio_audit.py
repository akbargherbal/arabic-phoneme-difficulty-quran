#!/usr/bin/env python3
"""Audit donor reciter directories for per-verse coverage of the shortlist.

LISTING ONLY: this reads the bucket's top-level reciter prefixes and lists each
directory's objects to see which of the shortlisted ``SSSAAA.mp3`` files exist.
No audio is downloaded here (a separate scratch step probes a few files).

Performance style is inferred from the directory name.  This was confirmed to
be the only available signal: ``_catalog.json``, ``_registry/registry.json``,
``_registry/registry_history/*.json`` and every ``_metadata/<dir>/reciter.json``
(all 31) carry riwayah, bitrate, verse counts and provenance, but **no**
style/type/category field and every ``note`` field is empty.  A literal name
match (Mujawwad/Muallim/Murattal) is preferred; otherwise a curated
common-knowledge map of well-known Murattal reciters is used and flagged with
``classification_source="common-knowledge"``.  ``warsh`` and the two Mujawwad
directories are excluded per policy.

Usage:
    python scripts/audio_audit.py \
        --shortlist data/pron/pron_shortlist_v2.csv \
        --out data/pron/reciter_audit.json
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor

BUCKET = (
    "gs://sheikh-fitzgerald-backup/ARABIC_DATA/"
    "QURAN_VERSE_BY_VERSE_RECITATIONS_DATASETS"
)
EXCLUDED_DIRS = {
    "warsh",  # Warsh riwaya: different text/numbering from Hafs Tanzil
    "Abdul_Basit_Mujawwad_128kbps",
    "Husary_128kbps_Mujawwad",
}
# Same reciter, two sources/encodings; counted once (see registry ids 13 & 71).
DUPLICATE_OF = {
    "Ahmed_ibn_Ali_al-Ajamy_128kbps_ketaballah.net": "ahmed_ibn_ali_al_ajamy_128kbps",
}
# Style keywords absent from bucket metadata; everyayah.com's verse-by-verse
# set is Murattal.  These are recited in the Murattal style by common
# knowledge; a literal name match (below) always takes precedence.
KNOWN_MURATTAL = {
    "Abdullaah_3awwaad_Al-Juhaynee_128kbps",
    "Abdullah_Basfar_192kbps",
    "Abdullah_Matroud_128kbps",
    "Abdurrahmaan_As-Sudais_192kbps",
    "Abu_Bakr_Ash-Shaatree_128kbps",
    "Ahmed_Neana_128kbps",
    "Ahmed_ibn_Ali_al-Ajamy_128kbps_ketaballah.net",
    "ahmed_ibn_ali_al_ajamy_128kbps",
    "Akram_AlAlaqimy_128kbps",
    "Hani_Rifai_192kbps",
    "Hudhaify_128kbps",
    "Husary_128kbps",  # base Husary; _Mujawwad/_Muallim are named separately
    "Khaalid_Abdullaah_al-Qahtaanee_192kbps",
    "Mohammad_al_Tablaway_128kbps",
    "Muhammad_AbdulKareem_128kbps",
    "Muhammad_Ayyoub_128kbps",
    "Muhammad_Jibreel_128kbps",
    "Muhsin_Al_Qasim_192kbps",
    "Nasser_Alqatami_128kbps",
    "Sahl_Yassin_128kbps",
    "Salaah_AbdulRahman_Bukhatir_128kbps",
    "Salah_Al_Budair_128kbps",
    "Saood_ash-Shuraym_128kbps",
    "Yaser_Salamah_128kbps",
    "Yasser_Ad-Dussary_128kbps",
    "aziz_alili_128kbps",
}


def classify(dirname: str) -> tuple[str, str]:
    """Return (style, source); source is name-keyword/common-knowledge/none."""
    name = dirname.lower()
    if "mujawwad" in name:
        return "Mujawwad", "name-keyword"
    if "muallim" in name:
        return "Muallim", "name-keyword"
    if "murattal" in name:
        return "Murattal", "name-keyword"
    if dirname in KNOWN_MURATTAL:
        return "Murattal", "common-knowledge"
    return "unknown", "none"


def gcloud_ls(prefix: str) -> list[str]:
    out = subprocess.run(
        ["gcloud", "storage", "ls", prefix],
        capture_output=True, text=True, check=True,
    ).stdout
    return [line.strip() for line in out.splitlines() if line.strip()]


def list_reciter_dirs() -> list[str]:
    prefix = BUCKET.rstrip("/")
    dirs = set()
    for line in gcloud_ls(BUCKET + "/"):
        if not line.endswith("/"):
            continue
        stripped = line.rstrip("/")
        if stripped == prefix:  # the listing echoes the bucket itself
            continue
        name = stripped.rsplit("/", 1)[-1]
        if name and not name.startswith("_"):
            dirs.add(name)
    return sorted(dirs)


def coverage_for_dir(dirname: str, keys: set[str]) -> dict:
    wanted = {key for key in keys}
    present: set[str] = set()
    for line in gcloud_ls(f"{BUCKET}/{dirname}/"):
        base = line.rsplit("/", 1)[-1]
        if base.endswith(".mp3"):
            key = base[:-4]
            if key in wanted:
                present.add(key)
    return {
        "dir": dirname,
        "count": len(present),
        "missing": sorted(wanted - present),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--shortlist", default="data/pron/pron_shortlist_v2.csv")
    ap.add_argument("--out", default="data/pron/reciter_audit.json")
    args = ap.parse_args(argv)

    with open(args.shortlist, encoding="utf-8", newline="") as fh:
        keys = {row["key"] for row in csv.DictReader(fh)}

    dirs = list_reciter_dirs()
    to_scan = [d for d in dirs if d not in EXCLUDED_DIRS]

    results: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        for res in pool.map(lambda d: coverage_for_dir(d, keys), to_scan):
            results[res["dir"]] = res

    audit = []
    for dirname in dirs:
        style, source = classify(dirname)
        entry = {
            "dir": dirname,
            "classification": style,
            "classification_source": source,
            "excluded": dirname in EXCLUDED_DIRS,
            "duplicate_of": DUPLICATE_OF.get(dirname),
        }
        if dirname in results:
            entry["coverage"] = results[dirname]["count"]
            entry["total"] = len(keys)
            entry["full_coverage"] = results[dirname]["count"] == len(keys)
            entry["missing"] = results[dirname]["missing"]
        audit.append(entry)

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(
            {
                "bucket": BUCKET,
                "shortlist_count": len(keys),
                "style_metadata_note": (
                    "No style/type/category field exists in _catalog.json, "
                    "_registry/registry.json, _registry/registry_history/*.json "
                    "or _metadata/*/reciter.json; all 'note' fields are empty. "
                    "classification_source=name-keyword|common-knowledge|none."
                ),
                "reciters": audit,
            },
            fh, ensure_ascii=False, indent=2,
        )

    header = f"{'directory':<48} {'class':<9} {'src':<17} {'cov':>5} {'dup':<30} excl"
    print(header)
    print("-" * len(header))
    for e in sorted(
        audit,
        key=lambda x: (x["excluded"], x["classification_source"], -x.get("coverage", -1)),
    ):
        dup = e["duplicate_of"] or ""
        print(
            f"{e['dir']:<48} {e['classification']:<9} {e['classification_source']:<17} "
            f"{e.get('coverage', '-'):>5} {dup:<30} {'yes' if e['excluded'] else ''}"
        )
    print(f"\nwritten: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
