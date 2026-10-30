# Video file validation before loading torch (exists, decodes, minimum duration).

from __future__ import annotations

from pathlib import Path

import cv2

from . import status
from .results import ValidationResult

_VALID_VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}


def validate_video_input(
    video_path: str | Path,
    *,
    min_duration_s: float = 0.5,
    max_duration_s: float | None = None,
) -> ValidationResult:
    """Check path, extension, and that OpenCV can read at least one frame."""
    p = Path(video_path)
    if not str(video_path).strip():
        return ValidationResult(
            ok=False,
            status=status.INVALID_INPUT,
            user_message="No video file was provided.",
            technical_warnings=["missing_input_path"],
        )
    if not p.exists():
        return ValidationResult(
            ok=False,
            status=status.INVALID_INPUT,
            user_message="The uploaded file does not exist or could not be found.",
            technical_warnings=["input_path_not_found"],
            details={"video_path": str(p)},
        )
    if not p.is_file():
        return ValidationResult(
            ok=False,
            status=status.INVALID_INPUT,
            user_message="The uploaded path is not a video file.",
            technical_warnings=["input_path_not_file"],
            details={"video_path": str(p)},
        )

    warnings: list[str] = []
    if p.suffix.lower() not in _VALID_VIDEO_EXTS:
        warnings.append("video_extension_unexpected")

    cap = cv2.VideoCapture(str(p))
    if not cap.isOpened():
        return ValidationResult(
            ok=False,
            status=status.VIDEO_DECODE_ERROR,
            user_message="Could not read the video. It may be damaged or in an unsupported format.",
            technical_warnings=warnings + ["video_capture_open_failed"],
            details={"video_path": str(p)},
        )
    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        ok_read, _ = cap.read()
    finally:
        cap.release()

    if not ok_read:
        return ValidationResult(
            ok=False,
            status=status.VIDEO_DECODE_ERROR,
            user_message="Could not decode any frame from the video.",
            technical_warnings=warnings + ["video_no_decodable_frames"],
            details={"video_path": str(p)},
        )
    if fps <= 0.0:
        return ValidationResult(
            ok=False,
            status=status.VIDEO_DECODE_ERROR,
            user_message="Could not read timing information from the video.",
            technical_warnings=warnings + ["video_invalid_fps"],
            details={"video_path": str(p), "fps": fps},
        )
    if frame_count <= 0:
        return ValidationResult(
            ok=False,
            status=status.VIDEO_DECODE_ERROR,
            user_message="The video contains no valid frames to analyze.",
            technical_warnings=warnings + ["video_zero_frame_count"],
            details={"video_path": str(p), "frame_count": frame_count},
        )

    duration_s = float(frame_count / fps) if fps > 0.0 else 0.0
    if duration_s < min_duration_s:
        return ValidationResult(
            ok=False,
            status=status.INVALID_INPUT,
            user_message="The video is too short to analyze repetitions reliably.",
            technical_warnings=warnings + ["video_duration_too_short"],
            details={"duration_s": duration_s, "min_duration_s": min_duration_s},
        )
    if max_duration_s is not None and duration_s > max_duration_s:
        return ValidationResult(
            ok=False,
            status=status.INVALID_INPUT,
            user_message="The video is too long for the current analysis limits.",
            technical_warnings=warnings + ["video_duration_too_long"],
            details={"duration_s": duration_s, "max_duration_s": max_duration_s},
        )

    return ValidationResult(
        ok=True,
        status=status.OK,
        user_message="Valid video input.",
        technical_warnings=warnings,
        details={
            "video_path": str(p),
            "fps": fps,
            "frame_count": frame_count,
            "duration_s": duration_s,
            "width": width,
            "height": height,
        },
    )
