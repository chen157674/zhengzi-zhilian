
# Builds the analysis "package": user + PM-Ideal + per-rep paired anchors.
# full_analysis block 10: build_rdl_analysis_context → validate_anchors_contract (contracts.py).

from src.biomechanics.rdl.analysis_context.constants import (
    RDL_ANCHOR_NAMES,
    RIGHT_CHAIN_KEYPOINTS,
)
from src.biomechanics.rdl.analysis_context.reference_loader import load_rdl_reference
from src.biomechanics.rdl.analysis_context.anchor_resolver import resolve_rdl_anchor_frames
from src.biomechanics.rdl.analysis_context.anchor_pairing import build_rdl_anchor_pairs
from src.biomechanics.rdl.analysis_context.context_builder import build_rdl_analysis_context

__all__ = [
    "RDL_ANCHOR_NAMES",
    "RIGHT_CHAIN_KEYPOINTS",
    "load_rdl_reference",
    "resolve_rdl_anchor_frames",
    "build_rdl_anchor_pairs",
    "build_rdl_analysis_context",
]
