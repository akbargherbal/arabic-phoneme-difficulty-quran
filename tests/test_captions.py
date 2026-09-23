"""Caption-file contract tests for the pronunciation shortlist.

Every shortlisted key must have a uthmani and a simple caption under
``data/pron/captions/`` with the exact layout, exactly one [Lyrics] and one
[Verse] line, and no protected pronunciation codepoint lost versus the raw
edition text.
"""

import json
import os

import pytest

from aya_scoring import arabic

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DUALSCRIPT = os.path.join(ROOT, "data", "pron_shortlist_dualscript.jsonl")
CAPTIONS = os.path.join(ROOT, "data", "pron", "captions")

PREFIX = (
    "Solo male voice, unaccompanied. Clear precise Arabic diction, measured pace."
)
PROTECTED = sorted(arabic.PROTECTED_PRONUNCIATION_CODEPOINTS)


def _records():
    with open(DUALSCRIPT, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


RECORDS = _records() if os.path.exists(DUALSCRIPT) else []


@pytest.mark.parametrize("record", RECORDS, ids=lambda r: r["key"])
def test_caption_layout_and_protected_marks(record):
    for script in ("uthmani", "simple"):
        path = os.path.join(CAPTIONS, f"{record['key']}_{script}.txt")
        assert os.path.exists(path), path
        with open(path, encoding="utf-8") as fh:
            content = fh.read()

        assert content.endswith("\n")
        assert not content.endswith("\n\n"), "no trailing blank line"
        lines = content.split("\n")
        assert lines[0] == PREFIX
        assert lines[1] == "[Lyrics]"
        assert lines[2] == "[Verse]"
        assert lines[3] == record[f"{script}_clean"]
        # Nothing but the single final newline after the text.
        assert lines[4:] == [""]
        assert content.count("[Lyrics]") == 1
        assert content.count("[Verse]") == 1

        raw = record[f"{script}_raw"]
        for cp in PROTECTED:
            ch = chr(cp)
            assert content.count(ch) == raw.count(ch), (
                f"protected U+{cp:04X} count changed for {record['key']} {script}"
            )


def test_caption_count():
    files = [f for f in os.listdir(CAPTIONS) if f.endswith(".txt")]
    assert len(files) == len(RECORDS) * 2
