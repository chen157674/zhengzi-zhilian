# RDL Form Analyzer

2D computer vision system for Romanian Deadlift technique analysis and repetition-level feedback.

## Overview

RDL Form Analyzer takes a 2D side-view video of a Romanian Deadlift (RDL) and returns structured, per-repetition technique feedback. The pipeline:

- detects the lifter in each frame (YOLOv8);
- estimates 2D body pose (RTMPose-L);
- cleans and smooths the keypoint time series;
- segments the video into individual repetitions;
- normalizes the movement and compares it against an ideal reference (PM-Ideal);
- runs RDL-specific biomechanical detectors;
- aggregates evidence into per-repetition feedback.

Results are available both through a **Flet web interface** and a **command-line interface**.

> This project is a portfolio / final-year academic project. It is **not** medical advice and **not** a replacement for a qualified coach. See [Limitations](#limitations).

## Screenshots

### App upload screen

![RDL Form Analyzer upload screen](assets/screenshots/app-home.png)

### Analysis results and repetition feedback

![RDL Form Analyzer analysis results](assets/screenshots/results-feedback.png)

### Overlay viewer

![RDL Form Analyzer overlay viewer](assets/screenshots/overlay-viewer.png)

## Features

- Person detection with YOLOv8
- 2D pose estimation with RTMPose-L
- Temporal keypoint cleaning (filtering, interpolation, smoothing)
- Automatic repetition segmentation
- RDL-specific technical detectors (bar path, arms, asymmetry, hip hinge, spine flexion, neck, lockout, knee dominance, range of motion)
- Evidence normalization and aggregation into per-repetition feedback
- Structured JSON output for integrations
- Overlay video generation for visual review
- Flet web interface with drag-and-drop upload

## Tech stack

Python 3.10 · OpenCV · PyTorch · Ultralytics YOLOv8 · RTMPose (MMPose / OpenMMLab) · NumPy / SciPy · Flet / FastAPI / Uvicorn

## Project structure

```text
rdl-form-analyzer/
  src/
    app/                    # Flet UI + pipeline subprocess adapter (+ web_assets/)
    pipeline/               # Orchestration: run_full_analysis
    pose/                   # Detection, person selection, RTMPose, extraction to NPZ
    pose_cleaning/          # Raw -> clean keypoints
    preprocessing/          # Video orientation / flip
    biomechanics/
      normalization/        # Sequence normalization
      rdl/                  # Segmentation, analysis context, detectors, feedback
    visualization/          # Overlay renderer and debug exports
    scripts/                # CLIs (pipeline, UI, overlay, diagnostics, evaluation)
    utils/                  # Shared paths and helpers
  configs/                  # RTMPose config (OpenMMLab)  [versioned]
  references/rdl/PM-Ideal/  # Ideal reference (JSON + NPZ) [versioned]
  weights/                  # YOLO / RTMPose weights       [not versioned]
  data/                     # Input videos                 [local, gitignored]
  outputs/                  # NPZ, overlays, debug, results [local, gitignored]
  tests/
  requirements.txt          # .venv  — pipeline runtime lock
  requirements-dev.txt      # .venv  — runtime + pytest
  requirements_flet.txt     # .venv_flet — UI
  corpus_config.json        # Optional local corpus evaluation config
```

**Versioned in Git:** source code, tests, `configs/`, the PM-Ideal reference, and directory placeholders (`.gitkeep`).
**Local only (gitignored):** virtual environments, model weights, input videos, and everything generated under `outputs/`.

## Tested environment

- Tested on **Linux / WSL2** with **Python 3.10**.
- Native **Windows** and **macOS** are **not currently verified**.
- Windows PowerShell commands below are provided as guidance only.
- **WSL2 / Linux is recommended**, mainly because the OpenMMLab stack (`mmcv` / `mmpose`) installs more reliably there.
- An NVIDIA GPU is optional; it speeds up inference but the pipeline also runs on CPU.
- Expect roughly **6 GB** of free disk for the environments, dependencies and models.

## Why two virtual environments?

The project intentionally uses **two isolated environments**:

| Environment  | Requirements file        | Purpose                                   |
|--------------|--------------------------|-------------------------------------------|
| `.venv`      | `requirements.txt`       | Heavy CV pipeline (Torch, MMPose, YOLO)   |
| `.venv_flet` | `requirements_flet.txt`  | Lightweight Flet web UI                   |

The UI runs in `.venv_flet` and **delegates the analysis to the pipeline as a subprocess**, pointed to by the `PIPELINE_PYTHON` environment variable. This keeps the Flet dependency stack (FastAPI, Uvicorn, Pydantic) fully separated from the Torch / OpenMMLab stack, which pin different versions of shared libraries. This separation is deliberate — do not mix packages between the two environments.

## Installation (Linux / WSL2)

### 1. Prerequisites

```bash
sudo apt update
sudo apt install -y software-properties-common git
sudo add-apt-repository ppa:deadsnakes/ppa -y
sudo apt update
sudo apt install -y python3.10 python3.10-venv python3.10-dev
python3.10 --version
```

### 2. Clone

```bash
git clone <your-repository-url> rdl-form-analyzer
cd rdl-form-analyzer
```

### 3. Pipeline environment (`.venv`)

```bash
python3.10 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip

# Step 1: PyTorch with CUDA 12.1 support
pip install torch==2.1.2 torchvision==0.16.2 --index-url https://download.pytorch.org/whl/cu121

# Step 2: openmim to manage OpenMMLab packages
pip install openmim==0.3.9

# Step 3: OpenMMLab components via mim
mim install mmengine==0.10.7
mim install mmcv==2.1.0
mim install mmdet==3.3.0

# Step 4: mmpose without its abandoned/unused deps
pip install mmpose==1.3.2 --no-deps

# Step 5: remaining dependencies
pip install -r requirements.txt --no-deps

deactivate
```

> **No GPU?** Replace Step 1 with the CPU build:
> `pip install torch==2.1.2 torchvision==0.16.2 --index-url https://download.pytorch.org/whl/cpu`
> The pipeline runs the same, just slower.

### 4. UI environment (`.venv_flet`)

```bash
python3.10 -m venv .venv_flet
source .venv_flet/bin/activate
python -m pip install --upgrade pip
pip install -r requirements_flet.txt
deactivate
```

### 5. (Optional) Development dependencies

To run the test suite, install the dev requirements into `.venv` (runtime lock + `pytest`):

```bash
source .venv/bin/activate
pip install -r requirements-dev.txt --no-deps
deactivate
```

## Windows PowerShell (untested)

The following is provided as guidance only and has **not** been verified. WSL2 is strongly recommended instead.

```powershell
py -3.10 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install torch==2.1.2 torchvision==0.16.2 --index-url https://download.pytorch.org/whl/cu121
pip install openmim==0.3.9
mim install mmengine==0.10.7
mim install mmcv==2.1.0
mim install mmdet==3.3.0
pip install mmpose==1.3.2 --no-deps
pip install -r requirements.txt --no-deps
deactivate

py -3.10 -m venv .venv_flet
.venv_flet\Scripts\Activate.ps1
pip install -r requirements_flet.txt
deactivate
```

When running commands, use `.venv\Scripts\python.exe` / `.venv_flet\Scripts\python.exe` and set variables with `$env:NAME = "value"`.

## Model weights

Weights are **not committed** to the repository. Download them and place them under `weights/`. Default paths are defined in `src/utils/paths.py`:

- `weights/yolov8s.pt` (~22 MB) — person detector
- `weights/rtmpose-l_simcc-coco_pt-aic-coco_420e-256x192-1352a4d2_20230127.pth` (~110 MB) — pose estimator
- `configs/rtmpose-l_8xb256-420e_coco-256x192.py` — RTMPose config (this one **is** versioned)

### Download (with `.venv` active)

```bash
source .venv/bin/activate
mkdir -p weights

# YOLOv8s (downloaded into the current directory, then moved)
python -c "from ultralytics import YOLO; YOLO('yolov8s.pt')"
mv yolov8s.pt weights/

# RTMPose-L (requires openmim in the environment)
mim download mmpose --config rtmpose-l_8xb256-420e_coco-256x192 --dest weights/

deactivate
```

If `mim` is not on the PATH, use `python -m mim download ...`. `mim download` also drops a `.py` config into `weights/`; you can ignore it — the pipeline uses the config in `configs/`. If the RTMPose file is downloaded under a different name, rename it to the expected filename above.

### Verify

```bash
ls -lh weights/yolov8s.pt
ls -lh weights/rtmpose-l_simcc-coco_pt-aic-coco_420e-256x192-1352a4d2_20230127.pth
```

If a weight is missing, the pipeline **fails early with a clear error** before loading Torch, rather than crashing later.

## Data / input video

There is **no sample video committed** to the repository — videos are kept local and are gitignored (`*.mp4`, etc.).

Place your own RDL side-view video under `data/`, for example `data/my_rdl_video.mp4`. For best results the clip should be:

- a **lateral (side) view** of the lifter;
- recorded with a **fixed camera**;
- showing the **full body** throughout the movement;
- an actual **Romanian Deadlift**;
- reasonably well lit and in focus.

## PM-Ideal reference

The full analysis compares the user's movement against an ideal reference stored in `references/rdl/PM-Ideal/`, which is versioned and includes:

- `ideal_segmentation_result.json`
- `ideal_pose_sequence_normalized_meta.json`
- `ideal_pose_sequence_normalized.npz` (required by the reference loader)

The reference directory is configurable via `FullAnalysisConfig.reference_dir` and loaded in `src/biomechanics/rdl/analysis_context/reference_loader.py`.

## Running the app

```bash
export PYTHONPATH=.
export PIPELINE_PYTHON="$PWD/.venv/bin/python"
export APP_HOST=127.0.0.1
export APP_PORT=8550
.venv_flet/bin/python -m src.scripts.run_app
```

Then open **http://127.0.0.1:8550** in your browser. The UI runs in `.venv_flet` and launches the analysis pipeline in a subprocess (`src.scripts.run_pipeline_json`) using `PIPELINE_PYTHON`. The first run is slower because it loads Torch and MMPose.

## Running the CLI

### Structured JSON analysis

```bash
PYTHONPATH=. .venv/bin/python -m src.scripts.run_pipeline_json \
  --video data/my_rdl_video.mp4 \
  --output outputs/result.json \
  --exercise RDL \
  --bundle-dir outputs/debug_runs/rdl
```

`--exercise` defaults to `RDL` (the only supported backend). You can also pass `--npz` with an already-generated **clean** NPZ instead of `--video`. The optional `--bundle-dir` creates a debug bundle that can later be used by the overlay renderer.

### Overlay video

Overlay rendering expects a debug bundle generated by the app or by `run_pipeline_json --bundle-dir`.

```bash
PYTHONPATH=. .venv/bin/python -m src.scripts.run_overlay_video \
  --bundle outputs/debug_runs/rdl/<bundle_id> \
  --dry-run
```

Drop `--dry-run` to actually render the MP4 (default output under `outputs/overlay/`).

### Pose extraction only (video -> clean NPZ)

```bash
PYTHONPATH=. .venv/bin/python -m src.scripts.run_video_to_clean_npz \
  --video data/my_rdl_video.mp4 \
  --out-dir outputs/npz
```

Additional diagnostic tools (per-detector debug scripts, segmentation, corpus evaluation, etc.) live under `src/scripts/`. They target development and validation and are not required for normal use.

## Environment variables

| Variable              | Used by                      | Default              | Notes |
|-----------------------|------------------------------|----------------------|-------|
| `PYTHONPATH`          | all CLIs / app               | —                    | Set to `.` so `python -m src.scripts.*` resolves |
| `PIPELINE_PYTHON`     | UI subprocess adapter        | `.venv/bin/python`   | Point to the pipeline interpreter when launching the app |
| `APP_HOST`            | `run_app`                    | `127.0.0.1`          | Optional |
| `APP_PORT`            | `run_app`                    | `8550`               | Optional |
| `PIPELINE_MAX_SECONDS`| UI subprocess adapter        | `3600`               | Subprocess timeout in seconds |
| `FLET_SECRET_KEY`     | `run_app`                    | dev default          | Local/dev only, not required to set |

## Testing

Requires the dev dependencies (`requirements-dev.txt`) in `.venv`:

```bash
PYTHONPATH=. .venv/bin/python -m pytest tests/ -q
```

## What is not included

- Model weights (`weights/*.pt`, `*.pth`)
- User input videos (`data/*.mp4`, ...)
- Generated outputs (`outputs/**`)
- Any other large local artifacts

## Limitations

- Not medical advice and not a replacement for a coach.
- Tested on controlled, side-view RDL videos.
- Accuracy is sensitive to camera angle, occlusion, pose-estimation quality and lighting.
- The analysis backend supports the Romanian Deadlift only.

## License

This project is released under the MIT License. See [LICENSE](LICENSE).

## More documentation

- Pipeline package: [`src/pipeline/README.md`](src/pipeline/README.md)
- Pose package: [`src/pose/README.md`](src/pose/README.md)
