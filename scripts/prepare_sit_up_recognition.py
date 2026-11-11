"""DVIDS767109的开发用目标标注；多人原片不冒充产品单人取景通过。"""
import hashlib,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment
configure_environment()
import av
import numpy as np
from fitness_app.pose_input import ContentPoseService,input_policy_snapshot
from fitness_app.frame_pipeline import usable_point
from fitness_app.sessions import IdentityContinuity

CACHE=ROOT/'data/verification/step-14/sit-up-dvids-767109-target-poses.npz'
SOURCE=ROOT/'data/datasets/development-movements/sit-up-dvids-767109.mp4'
ANNOTATION={'schema':'dataset-target-1','intervals_ms':[[64000,72000],[104000,112000]],
    'hip_center_bounds':[.30,.65,.60,.90],'actor':'woman performing bent-knee sit-ups; exclude instructor and foot holder',
    'basis':'Codex source-frame review, not independent expert labels',
    'review':'data/verification/step-14/sit-up-dvids-767109-target-review.png',
    'scope':'training only; target selection is not used by the live product'}


def run():
    digest=hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    if digest!='bd4260cacbc7842f307429145d413494c8c91c1c38bb4e437f01124de187fb22':raise ValueError('原片改变')
    meta={'source_sha256':digest,'source_bytes':SOURCE.stat().st_size,'source_modified_ns':SOURCE.stat().st_mtime_ns,
          'annotation':ANNOTATION,'policy':input_policy_snapshot(ContentPoseService)}
    if CACHE.exists():
        with np.load(CACHE,allow_pickle=False) as a:
            if json.loads(str(a['metadata']))!=meta:raise ValueError('目标标注缓存身份冲突')
            print(json.dumps({'cache_hit':True,'frames':len(a['timestamps_ms']),'selected':int(np.count_nonzero(a['person_counts']))}));return
    arrays={k:[] for k in ('timestamps_ms','points','sizes','person_counts','identity_accepted','source_person_counts','target_indices','all_points')}
    pose=None;part=None;continuity=IdentityContinuity()
    try:
        with av.open(str(SOURCE)) as video:
            for f in video.decode(video=0):
                stamp=round(float(f.time)*1000)
                interval=next((i for i,(start,end) in enumerate(ANNOTATION['intervals_ms']) if start<=stamp<end),None)
                if interval is None:continue
                if interval!=part:
                    if pose:pose.close()
                    pose=ContentPoseService(video=True);part=interval;continuity.reset()
                result=pose.detect(f.to_ndarray(format='rgb24'),stamp);poses=result['poses'];targets=[]
                for index,p in enumerate(poses):
                    if not all(usable_point(p[i]) for i in (23,24)):continue
                    x=(p[23]['x']+p[24]['x'])/2;y=(p[23]['y']+p[24]['y'])/2
                    left,top,right,bottom=ANNOTATION['hip_center_bounds']
                    if left<=x<=right and top<=y<=bottom:targets.append(index)
                chosen=targets[0] if len(targets)==1 else -1
                raw=np.full((33,5),np.nan,dtype=np.float32);all_points=np.full((2,33,5),np.nan,dtype=np.float32)
                for i,p in enumerate(poses):all_points[i]=[[v[k] for k in ('x','y','z','visibility','presence')] for v in p]
                accepted=False
                if chosen>=0:
                    raw=all_points[chosen].copy()
                    points=[{**p,'valid':usable_point(p)} for p in poses[chosen]]
                    accepted=continuity.accept({'width':f.width,'height':f.height,'framing':{'landmarks':points}})
                if not accepted:continuity.reset()
                row={'timestamps_ms':stamp,'points':raw,'sizes':(f.width,f.height),'person_counts':int(chosen>=0),
                     'identity_accepted':accepted,'source_person_counts':len(poses),'target_indices':chosen,'all_points':all_points}
                for key,value in row.items():arrays[key].append(value)
    finally:
        if pose:pose.close()
    with CACHE.open('xb') as output:np.savez_compressed(output,metadata=json.dumps(meta,sort_keys=True),**{k:np.asarray(v) for k,v in arrays.items()})
    print(json.dumps({'cache_hit':False,'frames':len(arrays['timestamps_ms']),'selected':sum(arrays['person_counts']),
                      'identity_accepted':sum(arrays['identity_accepted']),'multiple_source_frames':sum(n>1 for n in arrays['source_person_counts'])}),flush=True)


if __name__=='__main__':run()
