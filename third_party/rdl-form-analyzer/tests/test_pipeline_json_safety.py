"""Block 18A: JSON serialization safety and canonical pipeline statuses."""

from __future__ import annotations

import json

import numpy as np
import pytest

from src.pipeline import status
from src.scripts.run_pipeline_json import KNOWN_STATUSES, _to_json_safe, _write_json


# --------------------------------------------------------------------------- #
# 1) _to_json_safe: sanitize numpy types and non-finite values.
# --------------------------------------------------------------------------- #
def test_to_json_safe_non_finite_floats_become_none():
    assert _to_json_safe(np.nan) is None
    assert _to_json_safe(np.inf) is None
    assert _to_json_safe(-np.inf) is None
    assert _to_json_safe(float("nan")) is None
    assert _to_json_safe(float("inf")) is None


def test_to_json_safe_numpy_scalars():
    assert _to_json_safe(np.float32(1.5)) == 1.5
    assert isinstance(_to_json_safe(np.float32(1.5)), float)
    assert _to_json_safe(np.int64(3)) == 3
    assert isinstance(_to_json_safe(np.int64(3)), int)
    assert _to_json_safe(np.bool_(True)) is True
    assert _to_json_safe(np.bool_(False)) is False


def test_to_json_safe_ndarray_with_nan():
    arr = np.array([1.0, np.nan, np.inf, -np.inf, 2.5])
    assert _to_json_safe(arr) == [1.0, None, None, None, 2.5]


def test_to_json_safe_nested_structures():
    data = {
        "a": np.int64(1),
        "b": [np.float64(2.0), np.nan, {"c": np.bool_(True)}],
        "d": (np.float32(3.5), np.inf),
        "e": np.array([[1.0, np.nan], [2.0, 3.0]]),
    }
    out = _to_json_safe(data)
    assert out == {
        "a": 1,
        "b": [2.0, None, {"c": True}],
        "d": [3.5, None],
        "e": [[1.0, None], [2.0, 3.0]],
    }


# --------------------------------------------------------------------------- #
# 2) _write_json: strict JSON output (no NaN/Infinity), re-readable.
# --------------------------------------------------------------------------- #
def test_write_json_produces_strict_json(tmp_path):
    out = tmp_path / "out.json"
    payload = {
        "status": "ok",
        "score": np.nan,
        "pos": np.inf,
        "neg": -np.inf,
        "flag": np.bool_(True),
        "values": np.array([1.0, np.nan, 2.0]),
    }
    _write_json(out, payload)

    text = out.read_text(encoding="utf-8")
    assert "NaN" not in text
    assert "Infinity" not in text
    assert "-Infinity" not in text

    loaded = json.loads(text)
    assert loaded["status"] == "ok"
    assert loaded["score"] is None
    assert loaded["pos"] is None
    assert loaded["neg"] is None
    assert loaded["flag"] is True
    assert loaded["values"] == [1.0, None, 2.0]


def test_write_json_propagates_write_errors(tmp_path):
    # A directory as target => open('w') fails; the error must NOT be silenced.
    target_dir = tmp_path / "cannot_write"
    target_dir.mkdir()
    with pytest.raises(OSError):
        _write_json(target_dir, {"status": "ok"})


# --------------------------------------------------------------------------- #
# 3) ALL_STATUSES: shared single source of truth.
# --------------------------------------------------------------------------- #
def test_all_statuses_contains_expected_codes():
    assert status.OK in status.ALL_STATUSES
    assert status.NO_REPS_DETECTED in status.ALL_STATUSES
    assert status.PARTIAL_ANALYSIS in status.ALL_STATUSES
    assert status.FAILED in status.ALL_STATUSES


def test_known_statuses_alias_matches_all_statuses():
    assert KNOWN_STATUSES == status.ALL_STATUSES


def test_adapters_known_statuses_shares_all_statuses():
    from src.app.adapters import KNOWN_PIPELINE_STATUSES

    assert KNOWN_PIPELINE_STATUSES is status.ALL_STATUSES
