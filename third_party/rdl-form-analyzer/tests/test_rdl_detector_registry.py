"""Registry alignment: pipeline detector ids, evidence extractors, overlay VALID_DETECTORS."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from src.visualization.overlay.config import VALID_DETECTORS

EXPECTED_RDL_DETECTORS = frozenset({
    "bent_arms",
    "asymmetry",
    "bar_far",
    "hip_hinge",
    "knee_dominant",
    "lockout",
    "neck_movement",
    "rom",
    "spine_flexion",
})

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PIPELINE_PY = _REPO_ROOT / "src/biomechanics/rdl/detectors/pipeline.py"
_NORMALIZER_PY = _REPO_ROOT / "src/biomechanics/rdl/feedback/evidence_normalizer/normalizer.py"


def _detector_ids_assigned_in_pipeline_source() -> frozenset[str]:
    tree = ast.parse(_PIPELINE_PY.read_text(encoding="utf-8"))
    ids: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not isinstance(target, ast.Subscript):
                continue
            if not isinstance(target.value, ast.Name) or target.value.id != "detectors":
                continue
            key = target.slice
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                ids.add(key.value)
    return frozenset(ids)


def _extractor_registry_keys_from_normalizer_source() -> frozenset[str]:
    tree = ast.parse(_NORMALIZER_PY.read_text(encoding="utf-8"))

    def _keys_from_dict_literal(node: ast.Dict) -> frozenset[str]:
        keys: set[str] = set()
        for k in node.keys:
            if isinstance(k, ast.Constant) and isinstance(k.value, str):
                keys.add(k.value)
        return frozenset(keys)

    for node in ast.walk(tree):
        value: ast.AST | None = None
        name: str | None = None
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    name = target.id
                    value = node.value
                    break
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name):
                name = node.target.id
                value = node.value
        if name == "extractors" and value is not None:
            if not isinstance(value, ast.Dict):
                pytest.fail("extractors assignment is not a dict literal")
            return _keys_from_dict_literal(value)
    pytest.fail("extractors dict not found in normalizer.py")


def test_expected_rdl_detector_count():
    assert len(EXPECTED_RDL_DETECTORS) == 9


def test_overlay_valid_detectors_match_expected():
    assert VALID_DETECTORS == EXPECTED_RDL_DETECTORS


def test_evidence_normalizer_extractors_match_expected():
    assert _extractor_registry_keys_from_normalizer_source() == EXPECTED_RDL_DETECTORS


def test_pipeline_assigns_all_expected_detectors():
    assert _detector_ids_assigned_in_pipeline_source() == EXPECTED_RDL_DETECTORS


def test_this_module_does_not_import_app_components():
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name != "src.app.components"
        elif isinstance(node, ast.ImportFrom):
            assert node.module != "src.app.components"
            if node.module and node.module.startswith("src.app."):
                assert node.module != "src.app.components"
