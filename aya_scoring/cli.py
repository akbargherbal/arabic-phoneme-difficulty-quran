"""Command-line interface for the ayah difficulty scoring pipeline."""

from __future__ import annotations

import argparse
import os

from .config import ScoringConfig
from .pipeline import load_corpus, score_verses, write_outputs

DEFAULT_CORPUS_DIR = os.path.join("data", "quran")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="aya-scoring",
        description="Score Quran ayat for pronunciation-training difficulty.",
    )
    p.add_argument("--corpus", help="Path to a corpus JSON file.")
    p.add_argument(
        "--script",
        default="uthmani",
        help="Corpus script name under data/quran/ (used when --corpus is absent).",
    )
    p.add_argument("--min-word-count", type=int, default=3)
    p.add_argument("--max-word-count", type=int, default=12)
    p.add_argument("--no-tajweed", action="store_true", help="Disable Stage 4.")
    p.add_argument("--adjacency-across-words", action="store_true")
    p.add_argument("--density-basis", choices=["word", "letter"], default="word")
    p.add_argument("--config", help="JSON file overriding any ScoringConfig field.")
    p.add_argument("--out-dir", default="output")
    p.add_argument("--formats", default="csv,json",
                   help="Comma-separated: csv,json,parquet")
    p.add_argument("--sort-by", choices=["raw", "density"], default="raw")
    p.add_argument("--top", type=int, default=10, help="Preview N rows (0 = none).")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    config = ScoringConfig.from_json(args.config) if args.config else ScoringConfig()
    # Explicit CLI flags override the config file.
    config.min_word_count = args.min_word_count
    config.max_word_count = args.max_word_count
    config.adjacency_across_words = args.adjacency_across_words
    config.density_basis = args.density_basis
    if args.no_tajweed:
        config.enable_tajweed = False

    corpus_path = args.corpus or os.path.join(DEFAULT_CORPUS_DIR, f"{args.script}.json")
    if not os.path.exists(corpus_path):
        raise SystemExit(
            f"Corpus not found: {corpus_path}\n"
            f"Run: python download_corpus.py"
        )

    verses = load_corpus(corpus_path)
    rows, dropped = score_verses(verses, config)

    if args.sort_by == "density":
        rows.sort(key=lambda r: (r["density_score"], r["raw_score"]), reverse=True)

    written = write_outputs(
        rows, dropped, config, args.out_dir,
        formats=[f.strip() for f in args.formats.split(",") if f.strip()],
    )

    print(f"corpus          : {corpus_path}")
    print(f"total ayat      : {len(verses)}")
    print(f"kept (scored)   : {len(rows)}")
    print(f"dropped         : {len(dropped)}")
    if dropped:
        reasons: dict[str, int] = {}
        for d in dropped:
            key = d.reason.split("_of_")[0] if "duplicate" in d.reason else d.reason
            reasons[key] = reasons.get(key, 0) + 1
        print(f"drop reasons    : {reasons}")
    print(f"word range      : {config.min_word_count}-{config.max_word_count}")
    print(f"tajweed stage   : {'on' if config.enable_tajweed else 'off'}")
    print("files written   :")
    for path in written:
        print(f"  - {path}")

    if args.top and rows:
        print(f"\nTop {args.top} by {args.sort_by}:")
        print(f"{'surah:ayah':>10}  {'w':>2} {'raw':>4} {'dens':>6}  text")
        for r in rows[: args.top]:
            loc = f"{r['surah']}:{r['ayah']}"
            print(f"{loc:>10}  {r['word_count']:>2} {r['raw_score']:>4} "
                  f"{r['density_score']:>6.2f}  {r['text']}")

    return 0
