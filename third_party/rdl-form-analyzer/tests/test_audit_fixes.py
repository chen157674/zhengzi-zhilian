"""Regression guard for specific audit fixes (clear defects)."""

from __future__ import annotations

import numpy as np

from src.biomechanics.rdl.detectors.asymmetry.rules import max_severity as asym_max_severity
from src.biomechanics.rdl.detectors.neck_movement.rules import _severity_rank as neck_rank
from src.biomechanics.rdl.segmentation.io import _json_default
from src.biomechanics.rdl.segmentation.signal_stats import moving_average


def test_neck_severity_rank_unknown_no_keyerror():
    assert neck_rank("grave") == 3
    # An unexpected value must not raise KeyError.
    assert neck_rank("posible") == 0  # type: ignore[arg-type]
    assert neck_rank("desconocido") == 0  # type: ignore[arg-type]


def test_asymmetry_max_severity_unknown_safe():
    assert asym_max_severity("media", "grave") == "grave"
    # Unknown severity is treated as rank 0 (no ValueError).
    assert asym_max_severity("xxx", "leve") == "leve"  # type: ignore[arg-type]


def test_json_default_handles_numpy_bool():
    assert _json_default(np.bool_(True)) is True
    assert _json_default(np.bool_(False)) is False
    assert isinstance(_json_default(np.int64(3)), int)
    assert isinstance(_json_default(np.float64(1.5)), float)


def test_moving_average_edge_padding_preserves_edges():
    # Constant signal: moving average with edge padding must return the same constant
    # (the zero-padding of mode="same" sank the edges).
    seq = np.full(10, 90.0, dtype=np.float64)
    out = moving_average(seq, 5)
    assert out.shape == seq.shape
    assert np.allclose(out, 90.0), out


def test_moving_average_win_one_is_identity():
    seq = np.array([1.0, 2.0, 3.0])
    assert np.allclose(moving_average(seq, 1), seq)
