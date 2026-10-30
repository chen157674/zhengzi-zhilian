# Pipeline status codes (ok, no_reps_detected, partial_analysis, …) for UI and JSON.

OK = "ok"
INVALID_INPUT = "invalid_input"
VIDEO_DECODE_ERROR = "video_decode_error"
NO_PERSON_DETECTED = "no_person_detected"
INSUFFICIENT_POSE_QUALITY = "insufficient_pose_quality"
UNKNOWN_ORIENTATION = "unknown_orientation"
WRONG_EXERCISE = "wrong_exercise"
NO_REPS_DETECTED = "no_reps_detected"
INVALID_SEGMENTATION = "invalid_segmentation"
INVALID_ANCHORS = "invalid_anchors"
PARTIAL_ANALYSIS = "partial_analysis"
FAILED = "failed"

# Canonical set of valid pipeline statuses.
# Single source of truth for validations in scripts/UI (avoids duplicate sets).
ALL_STATUSES = frozenset(
    {
        OK,
        INVALID_INPUT,
        VIDEO_DECODE_ERROR,
        NO_PERSON_DETECTED,
        INSUFFICIENT_POSE_QUALITY,
        UNKNOWN_ORIENTATION,
        WRONG_EXERCISE,
        NO_REPS_DETECTED,
        INVALID_SEGMENTATION,
        INVALID_ANCHORS,
        PARTIAL_ANALYSIS,
        FAILED,
    }
)

