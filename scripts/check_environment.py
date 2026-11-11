"""T01-02：一个已有训练片段的解码和真实 Full CPU 推理，不做整集验收。"""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT
from fitness_app.pose import PoseService
import av

sample = ROOT / "data/datasets/mydeadlift/Gerakan Benar/GB_S01_R01.mp4"
with av.open(str(sample)) as container, PoseService() as pose:
    stream = container.streams.video[0]
    checked = 0
    for frame in container.decode(stream):
        if checked >= 10:
            break
        checked += 1
        result = pose.detect(frame.to_ndarray(format="rgb24"), int(float(frame.time or 0)*1000))
        if result["poses"]:
            assert len(result["poses"][0]) == 33
            print(json.dumps({"sample": str(sample.relative_to(ROOT)), "frame_pts": frame.pts,
                              "frames_checked": checked, "poses": len(result["poses"]),
                              "landmarks": 33, "model": result["model"]}))
            break
    else:
        raise RuntimeError("样本未解出可用人物，不能宣称姿态输出通过")
    if not result["poses"]:
        raise RuntimeError("已解码帧中未取得人物姿态")
