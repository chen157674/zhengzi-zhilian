"""用第二套冻结连续视频表示训练受限子类分支，保留全部33类基准与拒判。"""
import hashlib,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment,project_path
configure_environment()
import numpy as np
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_limits
from fitness_app.action_expert import CHEST,OTHER,MINIMUM_SCORE,MINIMUM_MARGIN,select_temporal_training,labels_for,refine_chest
from fitness_app.semantic_model import SemanticPredictor
from fitness_app.motion_evidence import evaluate as motion_evaluate,apply as motion_apply
from fitness_app.video_refinement import policy_version as family_version
from fitness_app.research_extract import atomic_json
from scripts.train_chest_expert import cases,cached_baseline,read,sha,OUT,OLD
from scripts.temporal_expert_features import load_temporal
from scripts.partial_action_training import source_weights,allowed_mask,fit_linear,probabilities,standardize_frozen,assert_source_split
from scripts.train_partial_action_fusion import FROZEN_INDEX_SHA256,FROZEN_HEAD_SHA256
from scripts.train_semantic_fusion import metrics_for_predictions


def main(verify=False,features_only=False):
    index=OLD/'semantic-video-features.json';weight=ROOT/'data/models/semantic-action-classifier.joblib'
    if sha(index)!=FROZEN_INDEX_SHA256 or sha(weight)!=FROZEN_HEAD_SHA256:raise ValueError('原生产基线改变')
    rows,expected,observed,previous=cases();predictor=SemanticPredictor();family=read(OLD/'video-family-evaluation.json');release=read(OLD/'video-release-calibration.json')
    if family['head_sha256']!=predictor.info['sha256'] or release['head_sha256']!=predictor.info['sha256'] or family['policy']['refinement_version']!=family_version():raise ValueError('原复核缓存身份不同')
    families={r['id']:r for r in family['rows']};calibration={r['id']:r for r in release['validation']['predictions']}
    evaluation=[i for i,r in enumerate(rows) if r['partition']!='train'];baseline={i:cached_baseline(rows[i],predictor,families,calibration,observed) for i in evaluation}
    selected=select_temporal_training(rows,4);routed=[i for i in evaluation if baseline[i][0].get('action_id') in CHEST]
    assert_source_split([rows[i] for i in selected],[rows[i] for i in evaluation])
    requested=selected+routed
    plan={'schema_version':1,'scope':'all chest training positives plus source-diverse negatives; evaluation routing uses base output, never expected label',
        'production_version':predictor.info['version'],'negative_limit_per_original_class':4,
        'training':[{'id':rows[i]['id'],'source_sha256':rows[i]['source_sha256'],'group':rows[i]['group'],'allowed_labels':labels_for(rows[i])} for i in selected],
        'evaluation_ids':[rows[i]['id'] for i in evaluation],'routed_evaluation_ids':[rows[i]['id'] for i in routed],
        'all_other_evaluation_passthrough':True,'thresholds':{'score':MINIMUM_SCORE,'margin':MINIMUM_MARGIN}}
    plan_path=OUT/'temporal-chest-inputs.json'
    if plan_path.exists():
        if read(plan_path)!=plan:raise ValueError('冻结子类输入/路由计划改变，不覆盖')
    else:atomic_json(plan_path,plan)
    print(json.dumps({'stage':'frozen_plan','training':len(selected),'routed_evaluation':len(routed),'all_evaluation':len(evaluation)}),flush=True)
    matrix,feature_info=load_temporal(rows,requested,expected,observed)
    if features_only:print(json.dumps({k:v for k,v in feature_info.items() if k!='records'}),flush=True);return
    n=len(selected);classes=sorted([*CHEST,OTHER]);fit_rows=[]
    for i in selected:
        row=rows[i];fit_rows.append({**row,'action_id':row['action_id'] if row['action_id'] in CHEST else OTHER,'label_scope':{'allowed_actions':labels_for(row)}})
    weights=source_weights(fit_rows);scaler=StandardScaler().fit(matrix[:n]);scaled=scaler.transform(matrix)
    identity={'plan_sha256':sha(plan_path),'feature_policy_sha256':feature_info['policy_sha256'],'consumed':[{k:r[k] for k in ('id','pixel_signature','feature_sha256')} for r in feature_info['records']],
        'classes':classes,'training_allowed_sets':[r['label_scope']['allowed_actions'] for r in fit_rows],'weights':weights.tolist(),'C':.3,'max_iter':200}
    target=OUT/'temporal-chest-expert-candidate.npz';report_path=OUT/'temporal-chest-expert-training.json';fit_code=sha(Path(__file__))
    with threadpool_limits(limits=4):
        if verify:
            saved_report=read(report_path)
            if saved_report['candidate_sha256']!=sha(target):raise ValueError('已完成候选参数的摘要改变')
            with np.load(target,allow_pickle=False) as a:
                if json.loads(str(a['identity']))!=identity:raise ValueError('时序专家参数不属于相同冻结输入')
                np.testing.assert_array_equal(a['mean'],scaler.mean_);np.testing.assert_array_equal(a['scale'],scaler.scale_)
                model={'coef':a['coef'].copy(),'intercept':a['intercept'].copy(),'optimization':json.loads(str(a['optimization']))}
                fit_code=str(a['fit_code_sha256']) if 'fit_code_sha256' in a else saved_report.get('fit_code_sha256',saved_report['identity']['trainer_sha256'])
        else:model=fit_linear(scaled[:n],allowed_mask(fit_rows,classes,True),weights)
        scores=probabilities(scaled[n:],model)
    if not verify:
        temporary=target.with_suffix('.npz.part');owned=False
        try:
            with temporary.open('xb') as f:owned=True;np.savez_compressed(f,identity=json.dumps(identity,sort_keys=True),classes=np.asarray(classes),mean=scaler.mean_,scale=scaler.scale_,coef=model['coef'],intercept=model['intercept'],optimization=json.dumps(model['optimization'],sort_keys=True),fit_code_sha256=fit_code,runtime_enabled=False)
            temporary.replace(target)
        finally:
            if owned and temporary.exists():temporary.unlink()
    with np.load(target,allow_pickle=False) as a:
        restored=probabilities(standardize_frozen(matrix[n:],a['mean'],a['scale']),{'coef':a['coef'],'intercept':a['intercept']});np.testing.assert_allclose(restored,scores,atol=1e-12,rtol=0)
    score_by={i:v for i,v in zip(routed,scores)};outputs=[];version='experimental-temporal-chest-'+sha(target)[:12]
    for i in evaluation:
        row=rows[i];base,arrays,times=baseline[i];candidate=dict(base);expert_top=expert_score=expert_margin=None
        if i in score_by:
            values=score_by[i];order=np.argsort(values)[::-1];expert_top=classes[order[0]];expert_score=float(values[order[0]]);expert_margin=expert_score-float(values[order[1]])
            candidate=refine_chest(base,expert_top,expert_score,expert_margin,version)
            if candidate!=base:candidate=motion_apply(candidate,motion_evaluate(arrays,int(times[0]),int(times[-1]),candidate['action_id'],predictor.info['motion_evidence']['config']))
        elif candidate!=base:raise ValueError('未路由输入被修改')
        outputs.append({k:row[k] for k in ('id','source_sha256','group','partition')}|{'expected':row['action_id'],'baseline':base,'candidate':candidate,
            'expert_invoked':i in score_by,'expert_top':expert_top,'expert_score':expert_score,'expert_margin':expert_margin,'changed':base['action_id']!=candidate['action_id']})
    results={}
    for part in ('validation','diagnostic'):
        items=[r for r in outputs if r['partition']==part];results[part]={}
        for name in ('baseline','candidate'):
            predictions=[{'id':r['id'],'source_sha256':r['source_sha256'],'group':r['group'],'expected':r['expected'],'top':r[name].get('top_action_id') or 'unknown',
                'predicted':r[name]['action_id'],'score':r[name]['score'],'margin':r[name]['margin']} for r in items]
            results[part][name]=metrics_for_predictions(predictions)
    before=results['validation']['baseline'];after=results['validation']['candidate'];base_correct=round(before['accuracy_including_rejections']*193)
    if base_correct!=read(OUT/'motion-evidence-release.json')['raw_original_protocol']['correct_after']:raise ValueError('未重现原完整基准')
    failures=[]
    for action,value in after['per_class'].items():
        prior=before['per_class'][action]
        if value['recall'] is None or value['recall']<.8:failures.append(action+': below 80 percent or missing')
        if value['recall'] is not None and prior['recall'] is not None and value['recall']<prior['recall']:failures.append(action+': regression')
        if value['original_videos']<3 or value['groups']<2:failures.append(action+': insufficient source coverage')
    if after['negative_false_accepts']>before['negative_false_accepts']:failures.append('negative false accepts increased')
    if results['diagnostic']['candidate']['accuracy_including_rejections']<results['diagnostic']['baseline']['accuracy_including_rejections']:failures.append('diagnostic regression')
    if not model['optimization']['stationarity_passed']:failures.append('optimization stationarity insufficient')
    failures.append('independent source coverage, posture-error truth and complete R3 acceptance still pending')
    if sha(index)!=FROZEN_INDEX_SHA256 or sha(weight)!=FROZEN_HEAD_SHA256:raise ValueError('运行期间生产基线改变')
    report={'schema_version':1,'scope':'second frozen video encoder, selected training only, all33 base+expert evaluation with explicit passthrough; no independent benchmark claim',
        'identity':{'plan_sha256':sha(plan_path),'production_manifest_sha256':sha(ROOT/'data/models/semantic-action-classifier.json'),'production_weight_sha256':sha(weight),
            'previous_training_report_sha256':sha(OUT/'partial-view-fusion-training.json'),'feature_policy_sha256':feature_info['policy_sha256'],'trainer_sha256':sha(Path(__file__)),
            'family_cache_sha256':sha(OLD/'video-family-evaluation.json'),'calibration_sha256':sha(OLD/'video-release-calibration.json'),
            'expert_code_sha256':sha(ROOT/'fitness_app/action_expert.py')},
        'feature_extraction':feature_info,'fit_identity':identity,'optimization':model['optimization'],'train_clips':n,'evaluation_clips':len(evaluation),'routed_evaluation_clips':len(routed),
        'results':results,'rows':outputs,'changed_cases':sum(r['changed'] for r in outputs),'candidate_path':target.relative_to(ROOT).as_posix(),'candidate_sha256':sha(target),
        'serialization_clips_verified':len(routed),'fit_code_sha256':fit_code,'protected_passthrough_clips':len(evaluation)-len(routed),
        'new_training_runs':0 if verify else 1,'new_pose_inferences':0,'new_qwen_semantic_inferences':0,'new_visual_family_reviews':0,
        'runtime_enabled':False,'gate_passed':not failures,'gate_failures':failures,
        'limitations':['Class/creator/partition are sampling or validation metadata; image encoder receives only eight RGB frames.','Nonrouted cases keep real baseline results and remain in all denominators.',
            'Only existing training people were sampled; Wger stays evaluation-only.','Expert scores are uncalibrated and do not assess posture errors.']}
    if report_path.exists():
        prior=read(report_path)
        report['initial_training_run']=prior.get('initial_training_run') or {'new_training_runs':prior['new_training_runs'],
            'extraction':{k:prior['feature_extraction'].get(k) for k in ('cache_hits','new_encoder_calls','legacy_reused','total_cached_native_encodings','legacy_api_check','sequential_recoveries','median_encoder_ms')},
            'candidate_sha256':prior['candidate_sha256'],'fit_code_sha256':prior.get('fit_code_sha256',prior['identity']['trainer_sha256'])}
    report['required_native_feature_records']=sum(r.get('legacy') is None for r in feature_info['records'])
    report['legacy_feature_records']=sum(r.get('legacy') is not None for r in feature_info['records'])
    report['cumulative_encoder_calls_from_frozen_records']=report['required_native_feature_records']+(feature_info.get('legacy_api_check') or {}).get('new_encoder_calls',0)
    atomic_json(report_path,report)
    print(json.dumps({'training':n,'all_evaluation':len(evaluation),'routed':len(routed),'baseline_correct':base_correct,'candidate_correct':round(after['accuracy_including_rejections']*193),
        'changed':report['changed_cases'],'bench':after['per_class']['dumbbell_bench_press'],'pec_deck':after['per_class']['pec_deck_fly'],'new_encoder_calls':feature_info['new_encoder_calls'],
        'legacy_reused':feature_info['legacy_reused'],'cache_hits':feature_info['cache_hits'],'gate_passed':report['gate_passed'],'runtime_enabled':False},ensure_ascii=False),flush=True)


if __name__=='__main__':
    if sys.argv[1:]==['--verify-candidate']:main(True)
    elif sys.argv[1:]==['--check-features']:main(False,True)
    elif sys.argv[1:]:raise SystemExit('支持无参数拟合、--verify-candidate、--check-features')
    else:main()
