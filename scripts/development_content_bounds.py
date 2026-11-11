"""把已核公开开发视频的图像范围附回原姿态缓存；不用于个人视频或独立测试重算。"""
from functools import lru_cache
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,project_path
from fitness_app.content_bounds import bounds_signature,validate_content_bounds
from fitness_app.frame_pipeline import framing

AUDIT=ROOT/'data/verification/step-14/content-bounds-validation.json'


@lru_cache(maxsize=2)
def _read(modified,size):
    report=json.loads(AUDIT.read_text(encoding='utf-8'))
    if report.get('schema_version')!=1 or report.get('content_bounds_signature')!=bounds_signature():raise ValueError('开发图像范围证据不属于当前实现')
    return report


def apply_verified_bounds(frames,action,source_path,source_sha256):
    stat=AUDIT.stat();report=_read(stat.st_mtime_ns,stat.st_size);path=project_path(source_path);key=path.relative_to(ROOT).as_posix()
    case=report['cases'].get(key)
    if not case:raise ValueError('该公开开发输入尚未核图像有效范围')
    source=path.stat()
    if (case['source_sha256'],case['source_bytes'],case['source_modified_ns'],case['frames'])!=(source_sha256,source.st_size,source.st_mtime_ns,len(frames)):raise ValueError('图像范围证据与原片／姿态帧数不符')
    cache=project_path(case['pose_cache_path']).stat()
    if (cache.st_size,cache.st_mtime_ns)!=(case['pose_cache_bytes'],case['pose_cache_modified_ns']):raise ValueError('已核姿态缓存发生变化')
    cursor=0
    for run in case['bounds_runs']:
        start,end=run['start_seq'],run['end_seq'];bounds=validate_content_bounds(run['bounds'])
        if start!=cursor or type(end) is not int or not start<end<=len(frames):raise ValueError('图像范围区间不连续')
        for frame in frames[start:end]:
            count=frame['framing']['person_count'];points=frame['framing']['landmarks']
            frame['framing']=framing(action,{'poses':[points] if count==1 else [[] for unused in range(count)],'content_bounds':bounds})
        cursor=end
    if cursor!=len(frames):raise ValueError('图像范围证据不完整')
    return frames
