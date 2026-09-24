"""Unit tests for the corrupt-file decode-diff helper."""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import pron_corrupt_decode_check as c  # noqa: E402


def test_diff_positive_and_percent():
    d, pct = c.diff(6.295510, 6.286937)
    assert d == pytest.approx(0.008573, abs=1e-6)
    assert pct == pytest.approx(0.13636, abs=1e-4)


def test_diff_zero():
    assert c.diff(4.5, 4.5) == (0.0, 0.0)


def test_diff_none():
    assert c.diff(None, 1.0) == (None, None)
    assert c.diff(1.0, None) == (None, None)
