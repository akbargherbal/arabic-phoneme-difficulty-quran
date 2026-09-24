"""Unit tests for the content-level audio quality checker.

These exercise the pure metric/flag logic in
``scripts/pron_audio_quality.py`` on synthetic waveforms, so they need no
donor audio on disk.  ``librosa`` is a dev/analysis dependency
(``requirements-dev.txt``); the tests skip if it is absent.
"""

import os
import sys

import numpy as np
import pytest

pytest.importorskip("librosa")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import pron_audio_quality as q  # noqa: E402

SR = 44100


def _noise(seconds: float, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return (rng.standard_normal(int(seconds * SR)) * 0.1).astype(np.float32)


def test_longest_run():
    assert q.longest_run(np.array([False, False])) == 0
    assert q.longest_run(np.array([True, True, False, True])) == 2
    assert q.longest_run(np.array([True, True, True])) == 3
    assert q.longest_run(np.zeros(5, dtype=bool)) == 0


def test_clipping_flag():
    y = np.ones(4 * SR, dtype=np.float32)
    m = q.compute_metrics(y, SR)
    assert m["clip_fraction"] == 1.0
    assert m["clip_max_run"] >= 3
    assert "clipping" in m["flags"]


def test_silence_flag_on_zeros():
    m = q.compute_metrics(np.zeros(5 * SR, dtype=np.float32), SR)
    assert m["silence_frame_pct"] > 99.0
    assert m["leading_silence_s"] > 2.0
    assert "silence" in m["flags"]


def test_edge_silence_flag():
    y = np.concatenate([
        np.zeros(int(2.5 * SR), dtype=np.float32),
        _noise(3.0, seed=0),
        np.zeros(int(2.5 * SR), dtype=np.float32),
    ])
    m = q.compute_metrics(y, SR)
    assert m["leading_silence_s"] > 2.0
    assert m["trailing_silence_s"] > 2.0
    assert "silence" in m["flags"]


def test_broadband_noise_clean():
    m = q.compute_metrics(_noise(4.0, seed=1), SR)
    assert "clipping" not in m["flags"]
    assert "silence" not in m["flags"]
    assert "bandwidth" not in m["flags"]


def test_narrowband_tone_flags_bandwidth():
    t = np.arange(SR, dtype=np.float32) / SR
    m = q.compute_metrics((0.5 * np.sin(2 * np.pi * 1000 * t)).astype(np.float32), SR)
    assert m["rolloff_median_hz"] < 10000.0
    assert "bandwidth" in m["flags"]
