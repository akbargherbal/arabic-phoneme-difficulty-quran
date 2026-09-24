"""Unit tests for the training-set assembler's pure plan/selection logic."""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import pron_assemble_training_set as a  # noqa: E402


def _row(key, ltype="A", **counts):
    row = {"key": key, "length_type": ltype}
    for c in a.REQUIRED_LETTERS:
        row[f"count_{c}"] = counts.get(f"count_{c}", 0)
    return row


def test_make_and_parse_stem_round_trip():
    stem = a.make_stem("Abu_Bakr_Ash-Shaatree_128kbps", "010014", "uthmani")
    assert stem == "Abu_Bakr_Ash-Shaatree_128kbps_010014_uthmani"
    assert a.parse_stem(stem) == ("Abu_Bakr_Ash-Shaatree_128kbps", "010014", "uthmani")


def test_parse_stem_rejects_bad_script_and_key():
    with pytest.raises(ValueError):
        a.parse_stem("Husary_128kbps_001001_bogus")
    with pytest.raises(ValueError):
        a.parse_stem("Husary_128kbps_12345_uthmani")


def test_covers_letters():
    rows = [_row("000001", count_ح=1, count_خ=0, count_ع=1, count_ض=0),
            _row("000002", count_ح=0, count_خ=1, count_ع=0, count_ض=1)]
    assert a.covers_letters(rows)
    assert not a.covers_letters(rows[:1])


def test_select_holdout_is_deterministic_and_satisfies_constraints():
    a_pool = [_row(f"{i:06d}", "A", count_ح=1) for i in range(1, 21)]
    b_pool = [_row(f"{i:06d}", "B", count_خ=1) for i in range(100, 121)]
    # inject one ayah per missing letter so coverage is always possible
    a_pool.append(_row("900001", "A", count_ع=1))
    b_pool.append(_row("900002", "B", count_ض=1))

    chosen1, attempt1 = a.select_holdout(a_pool, b_pool, seed=42)
    chosen2, attempt2 = a.select_holdout(a_pool, b_pool, seed=42)
    assert [r["key"] for r in chosen1] == [r["key"] for r in chosen2]
    assert attempt1 == attempt2
    assert sum(r["length_type"] == "A" for r in chosen1) == 6
    assert sum(r["length_type"] == "B" for r in chosen1) == 4
    assert a.covers_letters(chosen1)


def test_unique_simple_keys(tmp_path):
    keys = ["000001", "000002", "000003"]
    (tmp_path / "000001_simple.txt").write_bytes(b"shared\n")
    (tmp_path / "000002_simple.txt").write_bytes(b"shared\n")
    (tmp_path / "000003_simple.txt").write_bytes(b"unique\n")
    unique, duplicates = a.unique_simple_keys(str(tmp_path), keys)
    assert unique == {"000003"}
    assert duplicates == [["000001", "000002"]]


def test_build_plan_excludes_both_scripts_and_routes_holdout_to_val():
    rows = [_row("000001"), _row("000002"), _row("000003")]
    reciters = ["R1", "R2"]
    exclusions = {("R1", "000002")}
    plan = a.build_plan(rows, reciters, exclusions,
                        holdout_keys={"000003"}, smoke_keys={"000001"},
                        smoke_reciters=["R1", "R2"])

    train = [p for p in plan if p["split"] == "train"]
    val = [p for p in plan if p["split"] == "val"]
    smoke = [p for p in plan if p["split"] == "smoke"]

    # 3 keys x 2 reciters x 2 scripts = 12, minus 2 excluded, minus 4 held out
    assert len(train) == 6
    assert len(val) == 4
    assert len(smoke) == 4
    assert not any(p["reciter"] == "R1" and p["key"] == "000002" for p in plan)
    assert all(p["key"] == "000003" for p in val)
    assert all(p["key"] == "000001" for p in smoke)


def test_build_plan_rejects_smoke_outside_train():
    rows = [_row("000001")]
    with pytest.raises(ValueError):
        a.build_plan(rows, ["R1"], {("R1", "000001")},
                     holdout_keys=set(), smoke_keys={"000001"},
                     smoke_reciters=["R1"])


def test_find_forbidden():
    assert a.find_forbidden("clean text") == []
    assert a.find_forbidden("bad arabmaqamrock here") == ["arabmaqamrock"]
    assert a.find_forbidden("trailing ... marks") == ["..."]
    assert a.find_forbidden("ellipsis … char") == ["…"]


def test_read_shortlist(tmp_path):
    p = tmp_path / "s.csv"
    p.write_text(
        "key,surah,ayah,word_count,length_type,raw_score,density_score,"
        "count_ح,count_خ,count_ع,count_ض\n"
        "001001,1,1,4,A,23.0,5.75,2,0,0,0\n",
        encoding="utf-8",
    )
    rows = a.read_shortlist(str(p))
    assert rows[0]["key"] == "001001"
    assert rows[0]["count_ح"] == 2
    assert rows[0]["length_type"] == "A"
