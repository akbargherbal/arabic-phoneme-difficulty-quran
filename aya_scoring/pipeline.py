"""Pipeline orchestration: corpus loading, scoring, output writing (spec §9)."""

from __future__ import annotations

import json
import os
from dataclasses import asdict

from .config import ScoringConfig
from .filters import DroppedVerse, Verse, apply_hard_filters
from .scoring import score_verse


def load_corpus(path: str) -> list[Verse]:
    """Load a corpus JSON produced by ``download_corpus.py``."""
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    verses = data["verses"] if isinstance(data, dict) else data
    out: list[Verse] = []
    for v in verses:
        text = v["text"]
        out.append(
            Verse(
                surah=int(v["surah"]),
                ayah=int(v["ayah"]),
                text=text,
                word_count=len(text.split()),
            )
        )
    return out


def score_verses(
    verses: list[Verse], config: ScoringConfig
) -> tuple[list[dict], list[DroppedVerse]]:
    kept, dropped = apply_hard_filters(verses, config)
    rows = [score_verse(v, config) for v in kept]
    rows.sort(key=lambda r: (r["raw_score"], r["density_score"]), reverse=True)
    return rows, dropped


def write_outputs(
    rows: list[dict],
    dropped: list[DroppedVerse],
    config: ScoringConfig,
    out_dir: str,
    formats: list[str] | None = None,
    stem: str = "ayah_scores",
) -> list[str]:
    """Write the scored table (+ dropped-ayah report + config snapshot)."""
    os.makedirs(out_dir, exist_ok=True)
    formats = formats or ["csv", "json"]
    written: list[str] = []

    if not rows:
        return written

    columns = list(rows[0].keys())

    if "csv" in formats:
        import csv

        path = os.path.join(out_dir, f"{stem}.csv")
        with open(path, "w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)
        written.append(path)

    if "json" in formats:
        path = os.path.join(out_dir, f"{stem}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, ensure_ascii=False, indent=2)
        written.append(path)

    if "parquet" in formats:
        try:
            import pandas as pd  # noqa: WPS433

            path = os.path.join(out_dir, f"{stem}.parquet")
            pd.DataFrame(rows).to_parquet(path, index=False)
            written.append(path)
        except Exception as exc:  # pragma: no cover - optional dependency
            print(f"[warn] skipping parquet output: {exc}")

    if dropped:
        import csv

        path = os.path.join(out_dir, "dropped_ayahs.csv")
        with open(path, "w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(
                fh, fieldnames=["surah", "ayah", "text", "reason"]
            )
            writer.writeheader()
            writer.writerows(asdict(d) for d in dropped)
        written.append(path)

    path = os.path.join(out_dir, "scoring_config.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(config.to_json_dict(), fh, ensure_ascii=False, indent=2)
    written.append(path)

    return written
