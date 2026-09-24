"""Unit tests for the longest-silent-gap and loudness helpers."""

import os
import sys

import numpy as np
import pytest

pytest.importorskip("librosa")
pytest.importorskip("pyloudnorm")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import pron_audio_level_silence as m  # noqa: E402

SR = 44100


def _noise(seconds, seed=0):
    rng = np.random.default_rng(seed)
    return (rng.standard_normal(int(seconds * SR)) * 0.1).astype(np.float32)


def test_longest_true_run():
    assert m.longest_true_run(np.array([False, True, True, False, True,
                                        True, True, False])) == (3, 4)
    assert m.longest_true_run(np.zeros(5, dtype=bool)) == (0, 0)
    assert m.longest_true_run(np.ones(4, dtype=bool)) == (4, 0)


def test_longest_silent_run_finds_middle_gap():
    y = np.concatenate([_noise(1.0, seed=1),
                        np.zeros(4 * SR, dtype=np.float32),
                        _noise(1.0, seed=2)])
    r = m.longest_silent_run(y, SR)
    assert 3.8 <= r["longest_silent_run_s"] <= 4.2
    assert 0.8 <= r["silent_run_position"]["start_s"] <= 1.2
    assert (r["silent_run_position"]["end_s"]
            - r["silent_run_position"]["start_s"]
            == pytest.approx(r["longest_silent_run_s"], abs=0.02))


def test_longest_silent_run_none_on_noise():
    r = m.longest_silent_run(_noise(3.0, seed=3), SR)
    assert r["longest_silent_run_s"] < 3.0


def test_integrated_lufs_finite_for_tone():
    t = np.arange(SR * 2, dtype=np.float32) / SR
    y = (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    lufs = m.integrated_lufs(y, SR)
    assert lufs is not None and np.isfinite(lufs)
