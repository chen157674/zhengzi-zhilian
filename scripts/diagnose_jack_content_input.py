"""定位留边候选的3→2周期回归；只提取新增输入一次，后续复用精确浮点缓存。"""
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment
configure_environment()
from fitness_app.pose_input import ContentPoseService,input_policy_snapshot
from fitness_app.frame_pipeline import framing
from fitness_app.media import video_frames
from fitness_app.sessions import IdentityContinuity
from fitness_app.jumping_jack import JumpingJackCounter
from diagnose_jumping_jack import development_config


def main():
    folder=ROOT/'data/verification/step-19';source=ROOT/'data/datasets/development-movements/jumping-jacks-commons-segment.mp4'
    identity={'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'input_policy':input_policy_snapshot(ContentPoseService),'decoder_sha256':hashlib.sha256((ROOT/'fitness_app/media.py').read_bytes()).hexdigest()}
    path=folder/'jack-content-input-frames.json.gz'
    archive=json.loads(gzip.decompress(path.read_bytes())) if path.exists() else {'schema_version':1,'runs':[]}
    cached=next((r for r in archive['runs'] if r['identity']==identity),None)
    fresh=cached is None
    if fresh:
        observations=[]
        with ContentPoseService(video=True) as pose:
            for stamp,rgb in video_frames(source,{'origin_ms':0,'rotation':0}):
                observations.append({'stamp':stamp,'width':int(rgb.shape[1]),'height':int(rgb.shape[0]),'result':pose.detect(rgb,stamp)})
        cached={'identity':identity,'observations':observations};archive['runs'].append(cached)
        path.write_bytes(gzip.compress(json.dumps(archive,ensure_ascii=False,separators=(',',':')).encode('utf-8'),mtime=0))
    counter=JumpingJackCounter(development_config());continuity=IdentityContinuity();events=[];reasons=Counter();transitions=[];geometry=[];last=None
    for seq,row in enumerate(cached['observations']):
        output=row['result'];quality=framing('jumping_jack',output)
        frame={'frame_seq':seq,'captured_ms':row['stamp'],'width':row['width'],'height':row['height'],'framing':quality}
        usable=quality['usable'] and continuity.accept(frame)
        if usable:
            produced=counter.process(frame,{'stream_epoch':'bound-source','time_base':'source_ms'})
            reason=produced['observation']['sides']['none']['reason'];events.extend(produced['events'])
        else:
            counter.reset('tracking_lost');reason=quality['code']
        reasons[reason or 'observable']+=1
        state=(counter.tracker.phase,reason)
        if state!=last:transitions.append({'at_ms':row['stamp'],'phase':state[0],'reason':reason});last=state
        transform=output['input_transform']
        if not geometry or geometry[-1]['crop']!=transform['crop_pixels']:
            geometry.append({'at_ms':row['stamp'],'crop':transform['crop_pixels'],'reset':transform['tracker_reset']})
    report={'identity':identity,'scope':'exact registered 87-frame public video through candidate; raw candidate outputs cached before diagnosis; not independent truth',
            'fresh_inference':fresh,'frames':len(cached['observations']),'reference_before':3,'actual_count':len(events),
            'counter_sha256':hashlib.sha256((ROOT/'fitness_app/jumping_jack.py').read_bytes()).hexdigest(),'algorithm_version':counter.config.algorithm_version,
            'reference_regression':len(events)!=3,'events':events,'reason_counts':dict(reasons),'transitions':transitions,'geometry_changes':geometry}
    target=folder/'jack-content-input-diagnosis.json'
    if target.exists():
        prior=json.loads(target.read_text(encoding='utf-8'));history=prior.pop('previous_diagnostics',[])
        if prior.get('counter_sha256')!=report['counter_sha256']:history.append(prior)
        if history:report['previous_diagnostics']=history
    target.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in ('identity','previous_diagnostics')},ensure_ascii=False))


if __name__=='__main__':main()
