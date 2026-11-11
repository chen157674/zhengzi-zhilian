"""同源单视角、真实MediaPipe缓存迁移探针，失败不修改生产或补造标签。"""
import hashlib,json,sys
from collections import Counter,defaultdict
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment
configure_environment()
import joblib,numpy as np
from threadpoolctl import threadpool_limits
from fitness_app.plank_transfer import PlankTransferProbe,source_views,classify,camera_points
from fitness_app.ec3d_training import load,metrics
from fitness_app.research_extract import atomic_json
from fitness_app.counting_resources import create_counter
from scripts.prepare_movement_input import cached_frames


def single_view_check(bundle):
    labels,poses,_=load();groups=defaultdict(list)
    for i,row in enumerate(labels):
        if row[0]=='Plank' and row[1]=='Vidit':groups[tuple(map(str,row[:4]))].append(i)
    rows=[]
    for key,indices in groups.items():
        for view in source_views(poses[indices]):
            with threadpool_limits(limits=2):p=bundle['model'].predict_proba(view['feature'][None])[0]
            rows.append({'id':'/'.join(key),'label':int(key[2]),'side':view['side'],'yaw_degrees':view['yaw_degrees'],
                'prediction':int(classify(p[None],bundle['model'].classes_,bundle['threshold'])[0])})
    return {'scope':'synthetic_views_correlated_within_original_sequences_not_150_independent_clips',
        'metrics':metrics([r['label'] for r in rows],[r['prediction'] for r in rows]),'rows':rows}


def main():
    manifest=json.loads((ROOT/'data/models/open-source-correction/plank-transfer/manifest.json').read_text(encoding='utf-8'))
    path=ROOT/manifest['model_path']
    if hashlib.sha256(path.read_bytes()).hexdigest()!=manifest['sha256']:raise ValueError('权重摘要改变')
    bundle=joblib.load(path);probe=PlankTransferProbe(bundle);counter=create_counter('forearm_plank')
    source='dvids-640237-side';frames=cached_frames(source);rows=[];counts=Counter();candidates=Counter();raw_candidates=Counter();support=0;accepted=0
    with threadpool_limits(limits=2):
        for frame in frames:
            output=counter.process(frame,{'stream_epoch':'public-plank-transfer','identity_accepted':frame['identity_accepted']})
            supported=bool(output.get('intervals'));side=counter.measurement_side
            value=probe.update(frame,side=side,view='side',forearm_supported=supported)
            counts[value.get('reason') or value['status']]+=1;support+=supported
            if value.get('candidate_label') is not None:raw_candidates[str(value['candidate_label'])]+=1
            if value['status']=='candidate':candidates[value['source_label']]+=1;accepted+=1
            rows.append({'t':frame['captured_ms'],'support_confirmed':supported,'side':side,'result':value})
    cache=ROOT/'data/verification/step-14/dvids-640237-side-poses.npz';info=json.loads((ROOT/'data/datasets/development-movements/sources.json').read_text(encoding='utf-8'))
    asset=next(s for s in info['sources'] if s['id']==source)
    report={'scope':'actual_cached_public_camera_input_no_independent_specific_error_truth','model_sha256':manifest['sha256'],
        'single_projected_view':single_view_check(bundle),'native_public_clip':{'source_id':source,'source_sha256':asset['sha256'],
            'cache_path':cache.relative_to(ROOT).as_posix(),'cache_sha256':hashlib.sha256(cache.read_bytes()).hexdigest(),
            'frames':len(frames),'support_frames':support,'candidate_frames':accepted,'counts':dict(counts),'candidate_classes':dict(candidates),'raw_candidate_label_counts':dict(raw_candidates),'rows':rows},
        'runtime_enabled':False,'new_pose_inferences':0,'private_media_uploaded':False,'paid_api_calls':0,
        'temporal_confirmation':{'minimum_observations':3,'minimum_duration_ms':300,'current_missing_withdraws':True},
        'independent_error_accuracy_passed':False,'release_gate':manifest.get('release_gate'),'implementation_sha256':{n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in ('fitness_app/plank_transfer.py','scripts/evaluate_plank_transfer.py')}}
    atomic_json(ROOT/'data/verification/round3/plank-transfer-native-review.json',report)
    print(json.dumps({'single_view':report['single_projected_view']['metrics'], 'native_counts':dict(counts),
        'native_classes':dict(candidates),'support_frames':support,'frames':len(frames)},ensure_ascii=False),flush=True)

if __name__=='__main__':main()
