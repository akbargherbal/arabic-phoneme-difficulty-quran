"""Unit tests for the Task 12 low-quality verdict rule."""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import pron_abu_bakr_lowq_verify as v  # noqa: E402

REF_P05 = {
    "bandwidth_cutoff_hz": 1000.0,
    "hf_ratio_4k_5p5k_db": -23.0,
    "snr_proxy_db": 10.0,
    "consonant_proxy_db": -15.0,
}
REF_P95 = {
    "bandwidth_cutoff_hz": 16000.0,
    "hf_ratio_4k_5p5k_db": -13.0,
    "snr_proxy_db": 28.0,
    "consonant_proxy_db": -7.0,
}
BASE = {
    "bandwidth_cutoff_hz": 5000.0,
    "hf_ratio_4k_5p5k_db": -19.0,
    "snr_proxy_db": 14.0,
    "consonant_proxy_db": -11.0,
}


def _m(**over):
    return {**BASE, **over}


def test_keep_when_inside_on_2b_and_2d():
    r = v.classify(_m(), REF_P05, REF_P95)
    assert r["verdict"] == "KEEP"
    assert r["driving_metrics"] == []


def test_disqualify_hf_and_cutoff():
    r = v.classify(_m(bandwidth_cutoff_hz=500.0,
                      hf_ratio_4k_5p5k_db=-25.0), REF_P05, REF_P95)
    assert r["verdict"] == "DISQUALIFY"
    assert set(r["driving_metrics"]) == {"bandwidth_cutoff_hz",
                                         "hf_ratio_4k_5p5k_db"}


def test_disqualify_cons_and_snr():
    r = v.classify(_m(snr_proxy_db=8.0, consonant_proxy_db=-16.0),
                   REF_P05, REF_P95)
    assert r["verdict"] == "DISQUALIFY"
    assert set(r["driving_metrics"]) == {"snr_proxy_db", "consonant_proxy_db"}


def test_inconclusive_below_consonant_but_others_fine():
    r = v.classify(_m(hf_ratio_4k_5p5k_db=-25.0), REF_P05, REF_P95)
    assert r["verdict"] == "INCONCLUSIVE"
    assert r["below_p05"]["hf_ratio_4k_5p5k_db"] is True
    assert r["below_p05"]["snr_proxy_db"] is False


def test_inconclusive_above_reference_range():
    r = v.classify(_m(hf_ratio_4k_5p5k_db=-5.0), REF_P05, REF_P95)
    assert r["verdict"] == "INCONCLUSIVE"
    assert r["inside_5_95"]["hf_ratio_4k_5p5k_db"] is False


def test_flags_are_plain_bools():
    r = v.classify(_m(), REF_P05, REF_P95)
    assert all(isinstance(x, bool) for x in r["below_p05"].values())
    assert all(isinstance(x, bool) for x in r["inside_5_95"].values())


def test_percentile_rank():
    ref = sorted([1.0, 2.0, 3.0, 4.0])
    assert v.percentile_rank(0.5, ref) == 0.0
    assert v.percentile_rank(2.5, ref) == 50.0
    assert v.percentile_rank(9.0, ref) == 100.0
