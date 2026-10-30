# `pipeline`

Orchestration of the **full in-memory analysis** from a video (or a continuation from already-computed pose via scripts).

## Main modules

| File               | Role |
|--------------------|------|
| `full_analysis.py` | End-to-end RDL flow |
| `config.py`        | `FullAnalysisConfig`: pose, orientation, reference |
| `results.py`       | `PipelineResult`, serialization for CLI/UI |
| `validation.py`    | Video validation and data contracts |
| `status.py`        | Status constants (`ok`, `no_reps_detected`, ...) |
| `contracts.py`     | Shape checks for pose / segmentation / anchors |

Public API: `from src.pipeline import run_full_analysis, FullAnalysisConfig, ...`

## Flow (`run_full_analysis`)

```text
original video
  -> input validation
  -> raw pose (YOLO + RTMPose, probe + final pass)
  -> lateral orientation and flip if the subject faces left
  -> clean pose (pose_cleaning)
  -> RDL segmentation
  -> sequence normalization (user)
  -> analysis_context (user + PM-Ideal reference)
  -> RDL detectors (bar_far, arms, asymmetry, hip_hinge, ...)
  -> evidence normalization -> aggregation -> feedback report
  -> PipelineResult / dict for CLI, tests or UI
```

Everything happens **in memory**: `full_analysis` does not write NPZ/JSON/PNG by default.

## Result and statuses

- `run_full_analysis` returns a runtime dictionary. Its `pipeline_result` carries a
  `status` field drawn from `status.py` (e.g. `ok`, `invalid_input`,
  `no_person_detected`, `no_reps_detected`, `failed`).
- Non-fatal outcomes (no person, no repetitions, invalid input) are returned as
  **structured results**, not raised as exceptions.
- `results.py` provides the serialization used by the CLI and UI. The JSON produced
  by `src.scripts.run_pipeline_json` is a strict, NaN/Inf-safe encoding of that
  runtime data (see the script and tests for the current serialization contract).

## Runtime vs. lightweight data

- The in-memory (runtime) structure holds full arrays and objects used by detectors
  and visualization.
- Scripts and the debug bundle can derive a lighter representation for
  persistence/inspection; heavy arrays are not required to render feedback.
- Disk persistence (NPZ, plots, `debug_runs/`) is decided by the **scripts**, not by
  `full_analysis`.

## Collaborating packages

- **`src/pose`**: detection, person selection, RTMPose, orientation.
- **`src/pose_cleaning`**: filtering, interpolation, smoothing (raw -> clean).
- **`src/biomechanics/rdl/segmentation`**: repetitions and anchors (core, no plots).
- **`src/biomechanics/rdl/analysis_context`**: user-ideal pairing and anchor cache.
- **`src/biomechanics/rdl/detectors`**: metrics and rules per technical error.
- **`src/biomechanics/rdl/feedback`**: evidence, aggregation and feedback text.
- **`src/visualization`**: overlay renderer and debug exports (optional, from scripts).

## Common entry points

| Script | Use |
|--------|-----|
| `src.scripts.run_pipeline_json` | UI Flet subprocess; writes JSON |
| `src.scripts.run_pipeline` | Interactive CLI; can save segmentation and debug bundles |
| `run_full_analysis` (direct) | Tests and Python integrations |

## Ideal reference

- Default directory: `references/rdl/PM-Ideal/` (configurable via `FullAnalysisConfig.reference_dir`).
- Loaded in `src/biomechanics/rdl/analysis_context/reference_loader.py`.
- The quantitative user-vs-ideal comparison is embedded in the detectors and the
  analysis context; it is not a separate module.

## Notes

- `--exercise` in `run_pipeline_json` is reserved and defaults to `RDL`; the current
  backend is **RDL only**.
- Statuses such as `invalid_input`, `no_person_detected`, `no_reps_detected` are
  returned as structured results and do not depend on uncontrolled exceptions.
