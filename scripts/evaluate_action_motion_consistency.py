"""只用训练来源拟合类别运动支持域，再重放已缓存的完整33类预测。"""
import json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment,project_path
configure_environment()
import numpy as np
from fitness_app.action_motion_consistency import extract,training_family,support_bounds,family_prediction,apply_consistency,policy_signature,VERSION
from fitness_app.semantic_model import resources
from fitness_app.source_partitions import enforce_source_policy
from fitness_app.research_extract import atomic_json
from scripts.train_chest_expert import cases,read,sha,OUT,OLD
from scripts.train_semantic_fusion import metrics_for_predictions
from scripts.encoder_adaptation_data import inputs as encoder_inputs

POLICY=OUT/'action-motion-consistency-policy.json'
OBSERVATIONS=OUT/'action-motion-training-observations.json'
CALIBRATION=OUT/'action-motion-source-calibration.json'
REPORT=OUT/'action-motion-consistency-evaluation.json'
MIN_VIDEOS=3;MIN_GROUPS=2;MIN_CV_GROUPS=2


def collect_training(info):
    rows,expected,observed,_=cases();selected=[r for r in rows if r['partition']=='train' and training_family(r)];enforce_source_policy(selected);records=[]
    for row in selected:
        key=row['id'];pose=project_path(observed[key]['pose_cache']) if key in observed else OLD/(key+'-full-world-features.npz');semantic=project_path(observed[key]['semantic_cache']) if key in observed else project_path(row['cache'])
        if sha(pose)!=expected[key]['pose_cache_sha256'] or sha(semantic)!=expected[key]['semantic_cache_sha256']:raise ValueError('训练分类运动缓存身份改变')
        with np.load(pose,allow_pickle=False) as a:arrays={k:a[k].copy() for k in ('timestamps','points','sizes','people')}
        with np.load(semantic,allow_pickle=False) as a:times=a['timestamps'].astype(np.int64).tolist()
        geometry=extract(arrays,times[0],times[-1],info['motion_evidence']['config'])
        records.append({k:row[k] for k in ('id','source_sha256','group','action_id','partition','label_scope')}|{'family':training_family(row),'actual_ms':times,
            'pose_cache_sha256':expected[key]['pose_cache_sha256'],'semantic_cache_sha256':expected[key]['semantic_cache_sha256'],'geometry':geometry})
    return records


def coverage(records,decisions=None):
    output={}
    for family in ('arm_elevation','elbow_flexion'):
        all_rows=[r for r in records if r['family']==family];usable=[r for r in all_rows if r['geometry']['state']=='assessable']
        if decisions is not None:usable=[r for r in all_rows if decisions.get(r['id'])==family]
        output[family]={'candidate_windows':len(all_rows),'usable_windows':len(usable),'original_videos':len({r['source_sha256'] for r in usable}),'groups':len({r['group'] for r in usable})}
    return output


def calibrate(records):
    predictions=[];folds=[]
    for group in sorted({r['group'] for r in records}):
        held=[r for r in records if r['group']==group];held_hashes={r['source_sha256'] for r in held};training=[r for r in records if r['group']!=group and r['source_sha256'] not in held_hashes]
        assert group not in {r['group'] for r in training};assert not held_hashes&{r['source_sha256'] for r in training}
        bounds=support_bounds(training);folds.append({'held_group':group,'held_ids':[r['id'] for r in held],'training_groups':sorted({r['group'] for r in training}),'training_windows':len(training),'bounds':bounds})
        for row in held:
            decision=family_prediction(row['geometry'],{'bounds':bounds}) if bounds is not None else None
            predictions.append({'id':row['id'],'group':group,'source_sha256':row['source_sha256'],'expected':row['family'],'predicted':decision['family'] if decision else None,'geometry_state':row['geometry']['state']})
    errors=[r for r in predictions if r['predicted'] is not None and r['predicted']!=r['expected']];decision_by={r['id']:r['predicted'] for r in predictions};calibrated=coverage(records,decision_by);supported=coverage(records)
    failures=[];bounds=support_bounds(records)
    if bounds is None:failures.append('training support regions overlap or one family is missing')
    if errors:failures.append('source-disjoint calibration contains contradictory decisions')
    for family,value in supported.items():
        if value['original_videos']<MIN_VIDEOS or value['groups']<MIN_GROUPS:failures.append(family+': insufficient final training support')
        if calibrated[family]['groups']<MIN_CV_GROUPS:failures.append(family+': insufficient independently held calibration groups')
    accepted=not failures
    return {'schema_version':1,'method':'leave-one-source-group-out; same source SHA excluded across folds; abstain outside retained support or with disagreeing active sides',
        'training_coverage':supported,'calibration_coverage':calibrated,'all_candidate_windows':len(records),'decisions':sum(r['predicted'] is not None for r in predictions),
        'correct':sum(r['predicted']==r['expected'] for r in predictions),'contradictions':len(errors),'predictions':predictions,'folds':folds,
        'accepted':accepted,'failures':failures,'bounds':bounds,'independent_validation':False,'scope':'source-disjoint training calibration only; not33-class or posture-error accuracy'}


def fit():
    if POLICY.exists():raise ValueError('策略已经冻结；使用--check-policy或--evaluate，不重复拟合')
    info,reason=resources()
    if info is None:raise ValueError(reason)
    config=info['motion_evidence']['config'];records=collect_training(info)
    atomic_json(OBSERVATIONS,{'schema_version':1,'records':records,'source':'original training class supervision collapsed only when all allowed leaves share one motion family',
        'primary_manifest_sha256':sha(ROOT/'data/models/semantic-action-classifier.json'),'motion_reliability_config':config,'module_sha256':sha(ROOT/'fitness_app/action_motion_consistency.py'),
        'new_pose_inferences':0,'new_vision_inferences':0,'validation_observations_used':False,'specific_error_ground_truth':False})
    result=calibrate(records);atomic_json(CALIBRATION,result)
    policy={'version':VERSION,'runtime_enabled':False,'calibration_accepted':result['accepted'],'bounds':result['bounds'],'training_coverage':result['training_coverage'],'calibration_coverage':result['calibration_coverage'],
        'minimum_training_videos_per_family':MIN_VIDEOS,'minimum_training_groups_per_family':MIN_GROUPS,'minimum_calibration_groups_per_family':MIN_CV_GROUPS,
        'primary_manifest_sha256':sha(ROOT/'data/models/semantic-action-classifier.json'),'primary_weight_sha256':info['sha256'],'geometry_config':config,
        'observations_sha256':sha(OBSERVATIONS),'calibration_sha256':sha(CALIBRATION),'code':{p:sha(ROOT/p) for p in ('fitness_app/action_motion_consistency.py','scripts/evaluate_action_motion_consistency.py')},
        'scope':'cross-family proposal veto only; cannot identify normal versus hammer grip or correctness of technique'}
    policy['signature']=policy_signature(policy);atomic_json(POLICY,policy)
    print(json.dumps({'stage':'trained_motion_consistency_support','training_windows':len(records),'training_coverage':result['training_coverage'],'calibration_coverage':result['calibration_coverage'],
        'source_calibration_decisions':result['decisions'],'source_calibration_errors':result['contradictions'],'calibration_accepted':result['accepted'],'bounds':result['bounds'],'runtime_enabled':False,'new_model_calls':0},ensure_ascii=False),flush=True)


def check_policy():
    policy=read(POLICY)
    if policy['signature']!=policy_signature(policy) or policy['version']!=VERSION:raise ValueError('类别运动策略摘要不同')
    for path,value in policy['code'].items():
        if sha(ROOT/path)!=value:raise ValueError('类别运动代码版本改变')
    if sha(OBSERVATIONS)!=policy['observations_sha256'] or sha(CALIBRATION)!=policy['calibration_sha256']:raise ValueError('训练/校准依据摘要不同')
    if sha(ROOT/'data/models/semantic-action-classifier.json')!=policy['primary_manifest_sha256']:raise ValueError('原主干/几何观测策略改变')
    calibration=read(CALIBRATION);observations=read(OBSERVATIONS)
    if policy['calibration_accepted']!=calibration['accepted'] or policy['bounds']!=support_bounds(observations['records']):raise ValueError('已保存的支持域或校准状态不同')
    return policy


def metrics(rows,field):
    return metrics_for_predictions([{'id':r['id'],'source_sha256':r['source_sha256'],'group':r['group'],'expected':r['expected'],'predicted':r[field]['action_id'],
        'top':r[field].get('top_action_id') or 'unknown','score':r[field]['score'],'margin':r[field]['margin']} for r in rows])


def evaluate():
    policy=check_policy();source=OUT/'temporal-video-adapter-training.json';temporal=read(source);training,evaluation,routed,parent,expected,observed=encoder_inputs();lookup={r['id']:r for r in evaluation};route={r['id']:r for r in routed}
    if temporal['identity']['primary_weight_sha256']!=policy['primary_weight_sha256'] or temporal['identity']['routed_ids']!=[r['id'] for r in routed]:raise ValueError('缓存权重/咨询输入与本次基准不同')
    geometry={};results={};pose_reads=0
    for mode,condition in temporal['modes'].items():
        if sha(OUT/('temporal-video-'+mode+'.pt'))!=condition['candidate_sha256'] or sha(OUT/('temporal-video-'+mode+'-outputs.json'))!=condition['output_cache_sha256']:raise ValueError('已训练时序候选或预测缓存改变')
        if {r['id'] for r in condition['rows']}!=set(lookup) or len(condition['rows'])!=len(lookup):raise ValueError('完整原评估人口不同')
        outputs=[]
        for row in condition['rows']:
            original=lookup[row['id']]
            if row['source_sha256']!=original['source_sha256'] or row['expected']!=original['action_id'] or row['partition']!=original['partition']:raise ValueError('评估来源/原标签不同')
            key=row['id'];base=row['parent'];candidate=row['candidate']
            from fitness_app.action_motion_consistency import FAMILIES
            needed=FAMILIES.get(base.get('action_id')) and FAMILIES.get(candidate.get('action_id')) and FAMILIES[base['action_id']]!=FAMILIES[candidate['action_id']]
            if needed and key not in geometry:
                entry=route[key];pose=project_path(observed[key]['pose_cache']) if key in observed else OLD/(key+'-full-world-features.npz')
                if sha(pose)!=expected[key]['pose_cache_sha256']:raise ValueError('关节运动缓存身份不同')
                with np.load(pose,allow_pickle=False) as a:arrays={k:a[k].copy() for k in ('timestamps','points','sizes','people')}
                geometry[key]=extract(arrays,entry['actual_ms'][0],entry['actual_ms'][-1],policy['geometry_config']);pose_reads+=1
            guarded=apply_consistency(base,candidate,geometry.get(key,{'state':'insufficient','reason':'not_in_scope','sides':[]}),policy,research=True)
            outputs.append({k:row[k] for k in ('id','source_sha256','group','partition','expected')}|{'parent':base,'unguarded':candidate,'guarded':guarded,'raw_expert_details':row['details']})
        partitions={}
        for part in ('validation','diagnostic'):
            subset=[r for r in outputs if r['partition']==part];partitions[part]={name:metrics(subset,name) for name in ('parent','unguarded','guarded')}
        nonregression=all(partitions[part]['guarded']['accuracy_including_rejections']>=partitions[part]['parent']['accuracy_including_rejections'] for part in partitions)
        for action,value in partitions['validation']['guarded']['per_class'].items():
            prior=partitions['validation']['parent']['per_class'][action]
            if value['recall'] is not None and prior['recall'] is not None and value['recall']<prior['recall']:nonregression=False
        if partitions['validation']['guarded']['negative_false_accepts']>partitions['validation']['parent']['negative_false_accepts']:nonregression=False
        results[mode]={'metrics':partitions,'rows':outputs,'vetoes':sum(r['guarded'].get('classification_consistency',{}).get('state')=='proposal_vetoed' for r in outputs),'development_nonregression':nonregression}
    failures=[]
    for action,value in results['multiview']['metrics']['validation']['guarded']['per_class'].items():
        if value['recall'] is None or value['recall']<.8:failures.append(action+': below80 or missing')
        if value['original_videos']<3 or value['groups']<2:failures.append(action+': source coverage insufficient')
    if not policy['calibration_accepted']:failures.append('class-motion calibration gate failed')
    if not all(r['development_nonregression'] for r in results.values()):failures.append('complete pipeline regression')
    failures.append('independent error truth/real AI/mobile/full R3 acceptance pending')
    report={'schema_version':1,'identity':{'policy_sha256':sha(POLICY),'source_predictions_sha256':sha(source),'primary_manifest_sha256':policy['primary_manifest_sha256'],'evaluator_sha256':sha(Path(__file__))},
        'modes':results,'new_video_training_runs':0,'new_vision_inferences':0,'new_pose_inferences':0,'pose_caches_read':pose_reads,'new_unique_observations':len(geometry),
        'all_evaluation':len(evaluation),'class_motion_calibration_accepted':policy['calibration_accepted'],'runtime_enabled':False,'r3_release_gate_passed':not failures,'failures':failures,
        'limitations':['Only vetoes supported cross-family updates; does not choose a grip leaf or prove posture correctness.','Original development videos have already been inspected; this is not an independent test.','Frozen supplemental validation and reserved publishers are not consumed.']}
    atomic_json(REPORT,report)
    print(json.dumps({'complete_cached_replay':{mode:{part:{name:round(v['accuracy_including_rejections']*(193 if part=='validation' else 9)) for name,v in values.items()} for part,values in r['metrics'].items()} for mode,r in results.items()},
        'vetoes':{m:r['vetoes'] for m,r in results.items()},'pose_caches_read':pose_reads,'new_model_calls':0,'runtime_enabled':False,'r3_release_gate_passed':report['r3_release_gate_passed']},ensure_ascii=False),flush=True)


if __name__=='__main__':
    if sys.argv[1:]==['--fit']:fit()
    elif sys.argv[1:]==['--check-policy']:check_policy();print('frozen policy/code/training/calibration identities valid; no fit or model call')
    elif sys.argv[1:]==['--evaluate']:evaluate()
    else:raise SystemExit('使用--fit、--check-policy或--evaluate；拟合/校准与评估显式分开')
