"""新UI真实视频接线 fixture：真实解码、已有Full缓存，不重新推理，不用物理摄像头。"""
import os,json,hashlib,time
import numpy as np
from fitness_app import app as app_module
from fitness_app.paths import ROOT
from fitness_app.runtime import serve
case=json.loads((ROOT/'data/verification/step-19/barbell-posture-review.json').read_text(encoding='utf8'))['current_input_recheck']['cases']['row']
assert hashlib.sha256((ROOT/case['path']).read_bytes()).hexdigest()==case['source_sha256']
with np.load(ROOT/case['pose_cache'],allow_pickle=False) as archive:
    stamps={int(t):i for i,t in enumerate(archive['timestamps_ms'])}
    points=archive['points'].copy();counts=archive['person_counts'].copy()
class CachedPose:
    def __init__(self,video=True):pass
    def detect(self,rgb,timestamp):
        index=stamps[timestamp]
        raw=[dict(zip(('x','y','z','visibility','presence'),map(float,p))) for p in points[index]]
        time.sleep(.012) # 留出可观察的处理中UI；仅测试工厂，不影响产品性能。
        return {'poses':[raw] if counts[index]==1 else [],'model':'existing-real-Full-cache'}
    def close(self):pass
original=app_module.create_app
def create(config):
    return original({**config,'TESTING':True,'DATABASE_PATH':os.environ['FITNESS_DATABASE_PATH'],'PREVIEW_POSE_FACTORY':CachedPose})
app_module.create_app=create
serve(0)
