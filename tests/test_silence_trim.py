"""Unit tests for the non-destructive silence-trim offset rule."""

import os
import sys

import pytest

pytest.importorskip("librosa")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import pron_silence_trim_manifest as t  # noqa: E402


def test_no_cut_within_pad():
    assert t.excess_cut(0.0) == 0.0
    assert t.excess_cut(0.5) == 0.0
    assert t.excess_cut(1.0) == 0.0


def test_cut_only_the_excess():
    assert t.excess_cut(1.5) == 0.5
    assert t.excess_cut(3.8429) == 2.8429


def test_custom_pad():
    assert t.excess_cut(2.0, pad_s=0.5) == 1.5
    assert t.excess_cut(0.4, pad_s=0.5) == 0.0


def test_cut_is_never_negative():
    for measured in (-1.0, 0.0, 0.25):
        assert t.excess_cut(measured) >= 0.0
