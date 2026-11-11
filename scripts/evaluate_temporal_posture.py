"""时序纠错权重／特征／NumPy网络数值复现；不把数值一致当作动作准确率。"""
import argparse
from collections import Counter
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import time
import types

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment
configure_environment()
import numpy as np
import torch
from fitness_app.temporal_posture import TemporalKneeCandidate,temporal_features
from fitness_app.pretrained_resources import checked
from fitness_app.research_extract import atomic_json
from scripts.prepare_pretrained_correction import update


def review_source_cycles():
    """只重放现有真实姿态；周期缺失、稀疏或遮挡不由片段标签补全。"""
    from fitness_app.frame_pipeline import analysis_framing
    from fitness_app.posture_engine import reference_phase_counter
    from fitness_app.sessions import IdentityContinuity
    sources=(
        ('step-19/military-press-front-poses.npz','public_dvids_collection','train'),
        ('step-19/fitnessscape-press-poses.npz','fitnessscape_0I6q9NqK9tM','validation'),
        ('step-22/youtube-hG6sWbZmW1c-80000-97000-curated-sequence.npz','youtube-channel:UCqV2z4XYLcOLT27AmGT0M6w','train'),
        ('step-22/youtube-SJHlBLt1nbA-84000-95000-curated-sequence.npz','youtube-channel:UC7_LFnyBTIbN9e6p98YgIgw','validation'),
    )
    if {group for _,group,partition in sources if partition=='train'} & {group for _,group,partition in sources if partition=='validation'}:
        raise ValueError('时序候选来源组交叉')
    reviews=[]
    for relative,group,partition in sources:
        path=ROOT/'data/verification'/relative;counter=reference_phase_counter('barbell_overhead_press')
        identity=IdentityContinuity();candidate=TemporalKneeCandidate();reasons=Counter();cycles=[];unusable=0
        with np.load(path,allow_pickle=False) as archive:
            metadata=json.loads(str(archive['metadata']))
            times=archive['timestamps_ms' if 'timestamps_ms' in archive.files else 'timestamps']
            points=archive['points'];sizes=archive['sizes'];bounds=archive['bounds']
            people=archive['person_counts' if 'person_counts' in archive.files else 'people']
            if points.shape!=(len(times),33,5) or len(times)<2 or (np.diff(times)<=0).any():
                raise ValueError('原始时序缓存结构或源时间不符')
            for i,stamp in enumerate(times):
                poses=[[dict(zip(('x','y','z','visibility','presence'),map(float,p))) for p in points[i]]] if people[i]==1 else [[] for _ in range(int(people[i]))]
                frame={'captured_ms':int(stamp),'frame_seq':i,'width':int(sizes[i,0]),'height':int(sizes[i,1]),
                    'framing':analysis_framing('barbell_overhead_press',{'poses':poses,'content_bounds':bounds[i].tolist()})}
                accepted=identity.accept(frame);frame['identity_accepted']=accepted
                if not frame['framing']['usable'] or not accepted:
                    counter.reset('parts_or_target_missing');unusable+=1;continue
                output=counter.process(frame,{'identity_accepted':True})
                for state in output.get('observation',{}).get('sides',{}).values():
                    reasons[state.get('reason') or 'none']+=1
                for event in output.get('events',[]):
                    mask=(times>=event['start_ms'])&(times<=event['end_ms'])
                    result=candidate.observe(points[mask,:,:3],times[mask],np.minimum(points[mask,:,3],points[mask,:,4]),
                        action_id='barbell_overhead_press',phase_complete=True,single_person=bool((people[mask]==1).all()))
                    cycles.append({'event':event,'candidate':result})
            reviews.append({'pose_cache':path.relative_to(ROOT).as_posix(),'pose_cache_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                'source_sha256':metadata['source_sha256'],'source_group':group,'partition':partition,
                'frames':len(times),'mean_source_fps':(len(times)-1)*1000/float(times[-1]-times[0]),
                'unusable_frames':unusable,'complete_cycles':len(cycles),'cycles':cycles,'phase_reason_counts':dict(reasons),
                'specific_error_labels_available':False,'new_rgb_inference_frames':0})
    review={'scope':'real_cached_pose_phase_adapter_feasibility_not_specific_error_accuracy','sources':reviews,
        'phase_implementation_sha256':{name:hashlib.sha256((ROOT/'fitness_app'/name).read_bytes()).hexdigest()
            for name in ('posture_engine.py','overhead_press.py','cycles.py','frame_pipeline.py','sessions.py')},
        'complete_cycles':sum(r['complete_cycles'] for r in reviews),
        'candidate_inferences':sum(c['candidate']['status']=='candidate' for r in reviews for c in r['cycles']),
        'new_training_performed':False,'production_enabled':False,'private_media_uploaded':False}
    target=ROOT/'data/verification/round3/temporal-posture-reproduction.json'
    document=json.loads(target.read_text(encoding='utf-8'));document['cycle_adapter_review']=review
    atomic_json(target,document)
    print(json.dumps(review,ensure_ascii=False),flush=True)


def run():
    candidate=TemporalKneeCandidate();candidate.load();torch.set_num_threads(2)
    files={v['source_path']:v for v in candidate.resource['files']}
    def load(name,relative):
        record=files[relative];path=checked(record['path'],record)
        spec=importlib.util.spec_from_file_location(name,path)
        module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module);return module
    # 只导入已逐行检查的常量、特征和纯网络定义，不运行仓库入口／训练器。
    previous={name:sys.modules.get(name) for name in ('exercise_advisor','exercise_advisor.pose','exercise_advisor.pose.landmarks')}
    try:
        for name in ('exercise_advisor','exercise_advisor.pose'):
            module=types.ModuleType(name);module.__path__=[];sys.modules[name]=module
        load('exercise_advisor.pose.landmarks','src/exercise_advisor/pose/landmarks.py')
        reference=load('_fitness_temporal_features_reference','src/exercise_advisor/features/features.py')
        network=load('_fitness_temporal_network_reference','src/exercise_advisor/models/tcn.py')
        payload=torch.load(checked(candidate.weight['path'],candidate.weight),map_location='cpu',weights_only=True)
        model=network.ExerciseTCN(input_dim=222,n_error_classes=1,hidden_dim=128,n_layers=6,kernel_size=3,dropout=.3).eval()
        model.load_state_dict(payload['model_state'],strict=True)
        generator=np.random.default_rng(20261103)
        samples=[]
        for count in (40,100,160):
            base=generator.uniform(.2,.8,(1,33,3)).astype(np.float32)
            drift=np.sin(np.linspace(0,2*np.pi,count))[:,None,None].astype(np.float32)
            direction=generator.normal(0,.02,(1,33,3)).astype(np.float32)
            samples.append(('deterministic_geometry_'+str(count),base+drift*direction))
        cached=ROOT/'data/experiments/open-source-correction/user-1689-native-inputs.json.gz'
        if cached.exists():
            with gzip.open(cached,'rt',encoding='utf-8') as stream:cache=json.load(stream)
            rows=[r for r in cache['rows'] if len(r['pose_result']['poses'])==1][50:130]
            if len(rows)>=12:
                points=np.asarray([[[p[k] for k in ('x','y','z')] for p in row['pose_result']['poses'][0]] for row in rows],np.float32)
                samples.append(('real_pose_numeric_input_only_unsupported_lat_action',points))
        comparisons=[]
        for name,points in samples:
            expected=reference.interpolate_sequence(reference.build_feature_vector(points),100)
            actual=temporal_features(points)
            feature_error=float(np.max(np.abs(expected-actual)))
            with torch.inference_mode():expected_logit=float(model(torch.from_numpy(expected[None]))[0,0])
            actual_logit=candidate.logits(actual)
            expected_score=float(torch.sigmoid(torch.tensor(expected_logit)))
            actual_score=float(torch.sigmoid(torch.tensor(actual_logit)))
            if not np.allclose(expected,actual,rtol=1e-5,atol=5e-5) or abs(expected_score-actual_score)>2e-4:
                raise ValueError('时序特征或网络数值未对齐')
            comparisons.append({'input':name,'frames':len(points),'feature_max_abs_error':feature_error,
                'torch_logit':expected_logit,'numpy_logit':actual_logit,'score_abs_error':abs(expected_score-actual_score),
                'scope':'numeric_reproduction_not_fitness_error_truth'})
        x=temporal_features(samples[0][1]);candidate.logits(x)
        began=time.perf_counter()
        for _ in range(8):candidate.logits(x)
        elapsed=(time.perf_counter()-began)*1000/8
        rejected=candidate.observe(samples[0][1],np.arange(40)*33.333,np.ones((40,33)),
            action_id='lat_pulldown',phase_complete=True)
        if rejected['status']!='unassessable':raise ValueError('时序候选越过动作范围')
        result={'schema_version':1,'state':'numeric_reproduction_passed','source_commit':candidate.resource['source_commit'],
            'model_sha256':candidate.weight['sha256'],'parameter_count':network.count_parameters(model),
            'upstream_training_epochs':len(payload.get('history',{}).get('train_loss',[])),
            'numpy_inference_mean_ms':elapsed,'comparisons':comparisons,'scope_rejection':rejected,
            'source_dataset_access_obtained':False,'independent_error_accuracy_evaluated':False,
            'new_training_performed':False,'production_enabled':False,'private_media_uploaded':False}
        target=ROOT/'data/verification/round3/temporal-posture-reproduction.json'
        previous=json.loads(target.read_text(encoding='utf-8')) if target.exists() else {}
        if previous.get('cycle_adapter_review') and previous.get('model_sha256')==result['model_sha256']:
            result['cycle_adapter_review']=previous['cycle_adapter_review']
        atomic_json(target,result)
        record={**candidate.resource,'state':'numeric_reproduction_passed','reproduction_report':'data/verification/round3/temporal-posture-reproduction.json',
            'remaining_gates':['原片动作周期衔接','独立误报和漏报验证','发布门禁']}
        atomic_json(ROOT/'data/models/open-source-correction/exercise-advisor/manifest.json',record)
        update('temporal_quality_candidates','exercise-advisor-ohp-knees',record)
        print(json.dumps(result,ensure_ascii=False),flush=True)
    finally:
        for name,old in previous.items():
            if old is None:sys.modules.pop(name,None)
            else:sys.modules[name]=old


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--source-cycles',action='store_true')
    args=parser.parse_args()
    if args.source_cycles:review_source_cycles()
    else:run()
