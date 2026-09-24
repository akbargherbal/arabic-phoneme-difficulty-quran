"""Unit tests for the human-review sample quota allocation."""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import pron_human_review_zip as h  # noqa: E402

# Actual loudness_outlier counts from the Task 8 report.
COUNTS = {
    "Abdullah_Basfar_192kbps": 80,
    "Abdul_Basit_Murattal_192kbps": 70,
    "Husary_128kbps": 62,
    "Abu_Bakr_Ash-Shaatree_128kbps": 47,
    "Minshawy_Murattal_128kbps": 31,
    "Yaser_Salamah_128kbps": 14,
    "Hudhaify_128kbps": 5,
    "aziz_alili_128kbps": 2,
    "Muhammad_Ayyoub_128kbps": 1,
}


def test_real_counts_sum_and_bounds():
    q = h.allocate_quotas(COUNTS, 30, cap=4, minimum=1)
    assert sum(q.values()) == 30
    assert set(q) == set(COUNTS)
    for r, c in COUNTS.items():
        assert 1 <= q[r] <= min(4, c)


def test_small_contributors_capped_by_availability():
    q = h.allocate_quotas(COUNTS, 30, cap=4, minimum=1)
    assert q["Muhammad_Ayyoub_128kbps"] == 1
    assert q["aziz_alili_128kbps"] <= 2


def test_heavies_get_several():
    q = h.allocate_quotas(COUNTS, 30, cap=4, minimum=1)
    for r in ("Abdullah_Basfar_192kbps", "Abdul_Basit_Murattal_192kbps",
              "Husary_128kbps", "Abu_Bakr_Ash-Shaatree_128kbps"):
        assert q[r] == 4


def test_simple_proportional():
    q = h.allocate_quotas({"a": 100, "b": 1}, 4, cap=4, minimum=1)
    assert q == {"a": 3, "b": 1}


def test_empty_and_tiny_target():
    assert h.allocate_quotas({}, 5) == {}
    q = h.allocate_quotas({"a": 5, "b": 5, "c": 5}, 1, cap=4, minimum=1)
    assert sum(q.values()) == 3  # minimum per reciter is a floor


def test_deterministic():
    assert (h.allocate_quotas(COUNTS, 30)
            == h.allocate_quotas(COUNTS, 30))
