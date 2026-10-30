
# Temporal anchor names (RDL phases) and right-chain keypoints used to validate frames.

from __future__ import annotations

# User-vs-ideal comparison points: eccentric/concentric % + bottom.
RDL_ANCHOR_NAMES: tuple[str, ...] = (
    "ecc_0",
    "ecc_25",
    "ecc_50",
    "ecc_75",
    "ecc_100",
    "bottom",
    "con_0",
    "con_25",
    "con_50",
    "con_75",
    "con_100",
)

RDL_REFERENCE_DEFAULT_NAME = "PM-Ideal"

# Same lateral chain as pose_cleaning: an anchor frame must have these points finite when normalized.
RIGHT_CHAIN_KEYPOINTS: tuple[int, ...] = (6, 8, 10, 12, 14, 16)
