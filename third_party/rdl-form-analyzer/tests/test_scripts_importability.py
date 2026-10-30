"""Smoke: script modules import without running main() or heavy side effects."""

from __future__ import annotations

import importlib

import pytest

_SCRIPT_MODULES = [
    "src.scripts.run_pipeline_json",
    "src.scripts.run_overlay_video",
    "src.scripts.run_pipeline",
    "src.scripts.run_video_to_clean_npz",
    "src.scripts.run_pose_frame_overlay",
    "src.scripts.run_rdl_segmentation",
    "src.scripts.run_rdl_ideal_sequence_normalization",
    "src.scripts._pose_clean_npz",
    "src.scripts._pose_clean_rdl_tail",
    "src.scripts.run_asymmetry_debug",
    "src.scripts.run_bar_far_debug",
    "src.scripts.run_bent_arms_debug",
    "src.scripts.run_hip_hinge_debug",
    "src.scripts.run_knee_dominant_debug",
    "src.scripts.run_lockout_debug",
    "src.scripts.run_neck_movement_debug",
    "src.scripts.run_rom_debug",
    "src.scripts.run_spine_flexion_debug",
    "src.scripts.run_rdl_evidence_debug",
    "src.scripts.run_rdl_aggregation_debug",
    "src.scripts.run_rdl_feedback_debug",
]


@pytest.mark.parametrize("module_name", _SCRIPT_MODULES)
def test_script_module_imports_cleanly(module_name: str) -> None:
    importlib.import_module(module_name)


def test_run_app_import_skips_when_flet_stack_missing() -> None:
    try:
        importlib.import_module("src.scripts.run_app")
    except ModuleNotFoundError as exc:
        missing = (exc.name or str(exc)).lower()
        if "flet" in missing:
            pytest.skip(f"Flet UI stack not installed ({exc.name or exc})")
        raise


def test_pose_clean_private_helpers_expose_callables() -> None:
    from src.scripts._pose_clean_npz import load_pose_clean_npz
    from src.scripts._pose_clean_rdl_tail import run_rdl_success_tail_from_pose_clean

    assert callable(load_pose_clean_npz)
    assert callable(run_rdl_success_tail_from_pose_clean)
