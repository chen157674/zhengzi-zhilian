"""冻结已训练视频编码器，在训练来源上适配34类头，完整33类检查受限复核。"""
import hashlib,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment,project_path
configure_environment()
import numpy as np
from fitness_app.head_adaptation import UPPER,route_needed,apply_adapted,adapt_linear
from fitness_app.action_expert import select_temporal_training
from fitness_app.source_partitions import enforce_source_policy
from fitness_app.motion_evidence import evaluate as motion_evaluate
from fitness_app.semantic_model import SemanticPredictor
from fitness_app.research_extract import atomic_json
from scripts.train_chest_expert import cases,read,sha,OUT,OLD
from scripts.partial_action_training import source_weights,assert_source_split,allowed_mask,probabilities
from scripts.domain_adaptation_features import load_features
from scripts.train_semantic_fusion import metrics_for_predictions


def main(verify=False,*,roi=False,plan_only=False):
    rows,expected,observed,_=cases();selected=select_temporal_training(rows,4);training=[dict(rows[i]) for i in selected]
    prepared=read(OUT/'new-cc-reviewed-inputs.json')
    if hashlib.sha256((OUT/'new-cc-active-inputs.json').read_bytes()).hexdigest()!=prepared['frame_review_sha256']:raise ValueError('新训练窗口复核身份变化')
    added=[r for r in prepared['entries'] if r['eligible_training']]
    if len(added)!=1 or any(r['partition']!='train' for r in added):raise ValueError('只使用已就绪的新训练窗口')
    for row in added:training.append({**row,'label_scope':{'specificity':'leaf','allowed_actions':[row['action_id']],'leaf_label':row['action_id']},'actual_ms':row['review_actual_ms']})
    parent_path=OUT/'domain-adviser-evaluation.json';parent=read(parent_path);predictor=SemanticPredictor()
    if parent['identity']['primary_weight_sha256']!=predictor.info['sha256'] or parent['identity']['primary_manifest_sha256']!=sha(ROOT/'data/models/semantic-action-classifier.json'):raise ValueError('父候选主干或发布策略不同')
    evaluation=[r for r in rows if r['partition']!='train'];by_parent={r['id']:r for r in parent['rows']};routed=[]
    for row in evaluation:
        old=by_parent[row['id']]
        if old['source_sha256']!=row['source_sha256'] or old['expected']!=row['action_id']:raise ValueError('父候选输入/原标签不同')
        if route_needed(old['candidate']):routed.append(dict(row))
    assert_source_split(training,evaluation);enforce_source_policy(training)
    for row in training+routed:
        if 'actual_ms' in row:continue
        key=row['id'];semantic=project_path(observed[key]['semantic_cache']) if key in observed else project_path(row['cache'])
        if sha(semantic)!=expected[key]['semantic_cache_sha256']:raise ValueError('原语义帧缓存改变')
        with np.load(semantic,allow_pickle=False) as a:row['actual_ms']=a['timestamps'].astype(np.int64).tolist()
    new_pose_calls=0
    if roi:
        from scripts.prepare_upper_roi_inputs import prepare
        new_pose_calls=prepare(training+routed,expected,observed)
    prefix='domain-roi-head-adaptation' if roi else 'domain-head-adaptation'
    plan={'schema_version':1,'scope':'one new training window plus source-balanced old training; no new validation/reserved data; final fixed 200 CPU head steps',
        'parent_report_sha256':sha(parent_path),'training':[{k:r[k] for k in ('id','source_sha256','group','actual_ms','label_scope')} for r in training],
        'evaluation_ids':[r['id'] for r in evaluation],'routed_ids':[r['id'] for r in routed],'upper_scope':sorted(UPPER),
        'steps':200,'learning_rate':.001,'distillation_weight':.2,'anchor_strength':.001,'minimum_score':.8,'minimum_margin':.25,'low_margin_rescue_maximum_base_score':.7}
    if roi:
        plan['roi_code_sha256']=sha(ROOT/'fitness_app/upper_body_roi.py')
        plan['roi_inputs']=[{k:r[k] for k in ('id','roi_info','roi_pose_sha256')} for r in training+routed]
    plan_path=OUT/(prefix+'-plan.json')
    if plan_path.exists():
        if read(plan_path)!=plan:raise ValueError('适配计划已冻结，不按结果换输入/参数')
    else:atomic_json(plan_path,plan)
    print(json.dumps({'stage':'frozen_adaptation_plan','training':len(training),'new_training_windows':len(added),'routed':len(routed),'all_evaluation':len(evaluation)}),flush=True)
    if plan_only:return
    matrix,teacher,initial_weight,initial_bias,labels,feature_info=load_features(training+routed,roi=roi);n=len(training);weights=source_weights(training)
    mask=allowed_mask(training,labels,True);target=OUT/(prefix+'-candidate.npz');identity={'plan_sha256':sha(plan_path),'feature_policy_sha256':feature_info['policy_sha256'],
        'feature_records':[{k:r[k] for k in ('id','input_signature','feature_sha256')} for r in feature_info['records']],'labels':labels}
    fit_code=sha(Path(__file__))
    if verify:
        prior_report=read(OUT/(prefix+'-training.json'))
        if sha(target)!=prior_report['candidate_sha256']:raise ValueError('已完成适配参数的摘要改变')
        fit_code=prior_report.get('fit_code_sha256',prior_report['identity']['trainer_sha256'])
        with np.load(target,allow_pickle=False) as a:
            if json.loads(str(a['identity']))!=identity:raise ValueError('已适配分类头的输入身份不同')
            model={'coef':a['coef'].copy(),'intercept':a['intercept'].copy(),'history':json.loads(str(a['history'])),'protected_teacher_samples':int(a['protected_teacher_samples']),'steps':int(a['steps'])}
    else:
        model=adapt_linear(matrix[:n],mask,weights,teacher[:n],initial_weight,initial_bias,steps=200)
        temporary=target.with_suffix('.npz.part');owned=False
        try:
            with temporary.open('xb') as f:owned=True;np.savez_compressed(f,identity=json.dumps(identity,sort_keys=True),coef=model['coef'],intercept=model['intercept'],labels=np.asarray(labels),history=json.dumps(model['history']),
                protected_teacher_samples=model['protected_teacher_samples'],steps=model['steps'],runtime_enabled=False)
            temporary.replace(target)
        finally:
            if owned and temporary.exists():temporary.unlink()
    predictions={'unadapted':probabilities(matrix[n:],{'coef':initial_weight,'intercept':initial_bias}),'adapted':probabilities(matrix[n:],model)}
    with np.load(target,allow_pickle=False) as a:np.testing.assert_allclose(probabilities(matrix[n:],{'coef':a['coef'],'intercept':a['intercept']}),predictions['adapted'],atol=1e-12,rtol=0)
    routed_indices={r['id']:i for i,r in enumerate(routed)};outputs=[];version='experimental-domain-head-'+sha(target)[:12]
    for row in evaluation:
        key=row['id'];base=by_parent[key]['candidate'];result={name:dict(base) for name in predictions};details={}
        if key in routed_indices:
            index=routed_indices[key];pose=project_path(observed[key]['pose_cache']) if key in observed else OLD/(key+'-full-world-features.npz')
            with np.load(pose,allow_pickle=False) as a:arrays={k:a[k].copy() for k in ('timestamps','points','sizes','people')}
            times=routed[index]['actual_ms']
            for name,scores in predictions.items():
                values=scores[index];order=np.argsort(values)[::-1];top=labels[order[0]];score=float(values[order[0]]);margin=score-float(values[order[1]])
                motion=motion_evaluate(arrays,times[0],times[-1],top,predictor.info['motion_evidence']['config'])
                result[name]=apply_adapted(base,top,score,margin,motion,version+'-'+name);details[name]={'top':top,'score':score,'margin':margin,'motion':motion}
        outputs.append({k:row[k] for k in ('id','source_sha256','group','partition')}|{'expected':row['action_id'],'parent':base,**result,'head_details':details,'changed':result['adapted']['action_id']!=base['action_id']})
    results={}
    for part in ('validation','diagnostic'):
        cases_part=[r for r in outputs if r['partition']==part];results[part]={}
        for name in ('parent','unadapted','adapted'):
            values=[{'id':r['id'],'source_sha256':r['source_sha256'],'group':r['group'],'expected':r['expected'],'predicted':r[name]['action_id'],
                'top':r[name].get('top_action_id') or 'unknown','score':r[name]['score'],'margin':r[name]['margin']} for r in cases_part]
            results[part][name]=metrics_for_predictions(values)
    if round(results['validation']['parent']['accuracy_including_rejections']*193)!=170:raise ValueError('未重现父候选成绩')
    after=results['validation']['adapted'];before=results['validation']['parent'];failures=[]
    for action,value in after['per_class'].items():
        prior=before['per_class'][action]
        if value['recall'] is None or value['recall']<.8:failures.append(action+': below80 or missing')
        if value['recall'] is not None and prior['recall'] is not None and value['recall']<prior['recall']:failures.append(action+': regression')
        if value['original_videos']<3 or value['groups']<2:failures.append(action+': source coverage insufficient')
    if after['negative_false_accepts']>before['negative_false_accepts']:failures.append('negative false accepts increased')
    if results['diagnostic']['adapted']['accuracy_including_rejections']<results['diagnostic']['parent']['accuracy_including_rejections']:failures.append('diagnostic regression')
    failures.append('independent error truth/real AI/mobile/full R3 acceptance pending')
    report={'schema_version':1,'scope':'frozen trained video encoder, warm-started 34-class FC head; selective teacher preservation on training only; full original33 evaluation',
        'identity':identity|{'trainer_sha256':sha(Path(__file__)),'adaptation_code_sha256':sha(ROOT/'fitness_app/head_adaptation.py'),'checkpoint_sha256':feature_info['checkpoint_sha256']},
        'feature_extraction':feature_info,'train_clips':n,'new_training_windows':len(added),'all_evaluation':len(evaluation),'routed_evaluation':len(routed),'parameters':{k:plan[k] for k in ('steps','learning_rate','distillation_weight','anchor_strength','minimum_score','minimum_margin')},
        'training_history':model['history'],'protected_teacher_samples':model['protected_teacher_samples'],'new_head_training_runs':0 if verify else 1,'encoder_gradients':False,
        'results':results,'rows':outputs,'candidate_path':target.relative_to(ROOT).as_posix(),'candidate_sha256':sha(target),'serialization_cases':len(routed),
        'runtime_enabled':False,'gate_passed':not failures,'gate_failures':failures,'new_pose_inferences':new_pose_calls,'new_qwen_inferences':0,'fit_code_sha256':fit_code,
        'limitations':['One new source is training only; reserved new publisher and quarantined footage not read.','Only a classifier head is adapted, not posture-error supervision.',
            'Expanded upper-action routing and low-margin rescue are separate from old unchanged primary; unadapted-head control is retained.']}
    if roi:
        report['scope']='same fixed 106/204 protocol with label-independent upper-body ROI; frozen encoder, final 200 CPU head steps'
        preparation_path=OUT/'domain-roi-preparation.json';preparation=read(preparation_path)
        if preparation['plan_sha256']!=sha(plan_path):raise ValueError('ROI准备证据与固定计划不同')
        report['preparation']={'path':preparation_path.relative_to(ROOT).as_posix(),'sha256':sha(preparation_path),'new_pose_calls_before_training':sum(r['actual_pose_calls'] for r in preparation['new_pose_inputs'])}
    output=OUT/(prefix+'-training.json')
    if output.exists():
        prior=read(output);report['initial_run']=prior.get('initial_run') or {'new_head_training_runs':prior['new_head_training_runs'],'feature_extraction':{k:prior['feature_extraction'][k] for k in ('new_encoder_calls','cache_hits','inputs')}}
    audit_path=OUT/'face-pull-training-review.json'
    if audit_path.exists():report['training_equipment_phase_audit_sha256']=sha(audit_path)
    atomic_json(output,report)
    print(json.dumps({'train':n,'new_train':len(added),'all_evaluation':len(evaluation),'routed':len(routed),'correct':{name:round(results['validation'][name]['accuracy_including_rejections']*193) for name in ('parent','unadapted','adapted')},
        'face_pull':after['per_class']['cable_face_pull'],'bench':after['per_class']['dumbbell_bench_press'],'gate_passed':report['gate_passed'],'runtime_enabled':False,'new_feature_calls':feature_info['new_encoder_calls']},ensure_ascii=False),flush=True)


if __name__=='__main__':
    if sys.argv[1:]==['--upper-roi','--plan-only']:main(roi=True,plan_only=True)
    elif sys.argv[1:]==['--upper-roi','--verify-candidate']:main(True,roi=True)
    elif sys.argv[1:]==['--upper-roi']:main(roi=True)
    elif sys.argv[1:]==['--verify-candidate']:main(True)
    elif sys.argv[1:]:raise SystemExit('支持全帧或--upper-roi训练、--verify-candidate全缓存验证、ROI --plan-only')
    else:main()
