"""只增加已复核的局部视野训练片段；复用原625训练及204评估输入，不发布。"""
import hashlib,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment,project_path
configure_environment()
import numpy as np
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_limits
from fitness_app.world_features import WorldFeatures
from fitness_app.research_extract import atomic_json
from fitness_app.action_label_policy import reviewed_scope
from scripts.partial_action_training import source_weights,assert_source_split,allowed_mask,fit_linear,probabilities,metrics,partial_loss,standardize_frozen
from scripts.train_partial_action_fusion import load_features,gate,FROZEN_INDEX_SHA256,FROZEN_HEAD_SHA256
from scripts.probe_new_actions import validate_plan,validate_cached_row

OUT=ROOT/'data/verification/round3';OLD=ROOT/'data/verification/step-22'
def read(path):return json.loads(path.read_text(encoding='utf-8'))
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def merge_cases(previous,original,new_old_plan,new_old_probe,extra_plan,extra_probe):
    """原训练/评估身份保持，额外输入只能训练；不可把已有诊断作者挪入拟合。"""
    sources={r['id']:r for r in original['entries']+new_old_plan['entries']};observed={r['id']:r for r in new_old_probe['rows']+extra_probe['rows']}
    training={r['id']:r for r in previous['training']}
    evaluation={r['id']:r for part in ('validation','diagnostic') for r in previous['results']['production_head'][part]['predictions']}
    if set(training)&set(evaluation) or set(training)|set(evaluation)!={r['id'] for r in previous['identity']['consumed']}:raise ValueError('旧训练/评估消费清单不一致')
    rows=[]
    for identity in previous['identity']['consumed']:
        key=identity['id'];source=sources[key];meta=training.get(key) or evaluation[key]
        if any(meta[k]!=source[k] for k in ('source_sha256','group','action_id')):raise ValueError('原片/人物/标签与先前对照不同')
        if (key in training)!=(source['partition']=='train') or key in evaluation and meta['partition']!=source['partition']:raise ValueError('原拟合/评估分区改变')
        rows.append({**source,'label_scope':meta['label_scope'],'control_label':meta.get('control_label',source['action_id']),
            'origin':'retained_frozen_input','expected_feature':identity})
    existing={r['id'] for r in rows}
    for source in extra_plan['entries']:
        if source['partition']!='train' or source['id'] in existing:raise ValueError('新增局部视野清单只能增加独立标识的训练输入')
        validate_cached_row(source,observed[source['id']])
        label={'specificity':'leaf','family':source['action_id'],'allowed_actions':[source['action_id']],'leaf_label':source['action_id'],
            'reason':'developer review of actual source window for classification only; no full-body or posture truth'}
        rows.append({**source,'label_scope':label,'control_label':source['action_id'],'origin':'bounded_partial_view_training','expected_feature':None})
    assert_source_split([r for r in rows if r['partition']=='train'],[r for r in rows if r['partition']!='train'])
    return rows,observed


def input_identity():
    value={name:sha(OUT/name) for name in ('partial-label-fusion-training.json','recognition-probe-inputs.json','new-source-action-probe.json','partial-view-training-inputs.json','partial-view-action-probe.json','partial-view-label-review.json')}
    value['new_feature_caches']={r[k]:sha(project_path(r[k])) for r in read(OUT/'partial-view-action-probe.json')['rows'] for k in ('semantic_cache','pose_cache')}
    value['frozen_index_sha256']=sha(OLD/'semantic-video-features.json');value['production_head_sha256']=sha(ROOT/'data/models/semantic-action-classifier.joblib')
    return value


def verify_loaded_head(matrix,mask,weights,model):
    """检查既有参数在相同训练目标上的梯度，不执行任何优化步骤。"""
    import torch
    torch.set_num_threads(4)
    x=torch.as_tensor(matrix,dtype=torch.float64);target=torch.as_tensor(mask,dtype=torch.bool);w=torch.as_tensor(weights,dtype=torch.float64)
    coef=torch.tensor(model['coef'],dtype=torch.float64,requires_grad=True);bias=torch.tensor(model['intercept'],dtype=torch.float64,requires_grad=True)
    loss=partial_loss(x@coef.T+bias,target,w)+coef.square().sum()/(2*.3*w.sum());loss.backward()
    gradient=max(float(v.grad.detach().abs().max()) for v in (coef,bias))
    return {'objective':float(loss.detach()),'maximum_gradient':gradient,'stationarity_tolerance':1e-4,'stationarity_passed':gradient<=1e-4,
        'iterations':None,'original_max_iter':200,'new_optimizer_steps':0,'verification_mode':'owned fitted artifact; same data, scaler and objective; no refit','global_optimum_claimed':False}


def main(reuse_candidate=False):
    index=OLD/'semantic-video-features.json';weight=ROOT/'data/models/semantic-action-classifier.joblib';manifest=ROOT/'data/models/semantic-action-classifier.json'
    if sha(index)!=FROZEN_INDEX_SHA256 or sha(weight)!=FROZEN_HEAD_SHA256:raise ValueError('原识别基线改变')
    original=read(index);previous_path=OUT/'partial-label-fusion-training.json';previous=read(previous_path)
    if sha(project_path(previous['candidate_path']))!=previous['candidate_sha256']:raise ValueError('原625输入对照参数改变')
    old_plan_path=OUT/'recognition-probe-inputs.json';old_probe_path=OUT/'new-source-action-probe.json';plan_path=OUT/'partial-view-training-inputs.json';probe_path=OUT/'partial-view-action-probe.json'
    old_plan=read(old_plan_path);old_probe=read(old_probe_path);plan=read(plan_path);probe=read(probe_path);validate_plan(plan)
    for pp,pr in ((old_plan_path,old_probe),(plan_path,probe)):
        if pr['status']!='completed' or pr['plan_sha256']!=sha(pp):raise ValueError('输入探针未完整绑定冻结计划')
    for name,digest in plan['review_manifests'].items():
        if sha(OUT/name)!=digest:raise ValueError('实际画面复核清单改变')
    rows,observed=merge_cases(previous,original,old_plan,old_probe,plan,probe);encoder=WorldFeatures();vectors=[];consumed=[]
    review_path=OUT/'partial-view-label-review.json';label_review=read(review_path)
    if label_review['plan_sha256']!=sha(plan_path):raise ValueError('局部视野标签复核与冻结计划不匹配')
    for review in label_review['reviews']:
        case=next(r for r in rows if r['id']==review['id'])
        if case['origin']!='bounded_partial_view_training' or any(review[k]!=case[k] for k in ('source_sha256','start_ms','end_ms','crop','review_actual_ms')) or review['allowed_actions']!=['dumbbell_bench_press','incline_dumbbell_press'] or review['independent_error_truth'] is not False or sha(OUT/'gantner-incline-window.json')!=review['frame_manifest_sha256']:raise ValueError('局部视野子类范围复核无效')
        case['label_scope']={'specificity':'family','family':'dumbbell_bench_press_family','allowed_actions':review['allowed_actions'],'leaf_label':None,'reason':review['reason']}
    for i,row in enumerate(rows):
        if row['id'] in observed:
            source=observed[row['id']];semantic=project_path(source['semantic_cache']);pose=project_path(source['pose_cache']);new=True
        else:semantic=project_path(row['cache']);pose=OLD/(row['id']+'-full-world-features.npz');new=False
        value,identity=load_features(row,semantic,pose,encoder,expected=row['expected_feature'],new=new)
        vectors.append(value);consumed.append(identity)
        if (i+1)%200==0:print(json.dumps({'stage':'reuse_cached_features','inputs':i+1}),flush=True)
    training=[i for i,r in enumerate(rows) if r['partition']=='train'];valid=[i for i,r in enumerate(rows) if r['partition']=='validation'];diagnostic=[i for i,r in enumerate(rows) if r['partition']=='diagnostic']
    matrix=np.asarray(vectors,np.float32);scaler=StandardScaler().fit(matrix[training]);scaled=scaler.transform(matrix)
    with np.load(project_path(previous['candidate_path']),allow_pickle=False) as a:classes=a['classes'].tolist()
    train_rows=[rows[i] for i in training];weights=source_weights(train_rows)
    # 原625输入仍按原排他对照拟合；仅新复核为不确定的卧推窗口使用允许集合损失。
    fit_rows=[{**r,'label_scope':r['label_scope'] if r['origin']=='bounded_partial_view_training' else {'allowed_actions':[r['control_label']]}} for r in train_rows]
    identity=input_identity();target=OUT/'partial-view-fusion-candidate.npz';fit_code=sha(Path(__file__))
    with threadpool_limits(limits=4):
        if reuse_candidate:
            with np.load(target,allow_pickle=False) as a:
                if json.loads(str(a['fit_input_identity']))!=identity or a['classes'].tolist()!=classes:raise ValueError('现有候选不属于相同冻结输入')
                np.testing.assert_array_equal(scaler.mean_,a['mean']);np.testing.assert_array_equal(scaler.scale_,a['scale'])
                model={'coef':a['coef'].copy(),'intercept':a['intercept'].copy()};fit_code=str(a['fit_code_sha256'])
            model['optimization']=verify_loaded_head(scaled[training],allowed_mask(fit_rows,classes,True),weights,model)
        else:model=fit_linear(scaled[training],allowed_mask(fit_rows,classes,True),weights)
        scores=probabilities(scaled,model)
    results={name:metrics([rows[i] for i in indices],scores[indices],classes) for name,indices in (('validation',valid),('diagnostic',diagnostic))}
    reviewed={r['id']:r for r in read(OUT/'curl-validation-subtype-reviews.json')['reviews']};review_rows=[]
    for i in valid:
        row=rows[i];label=row['label_scope'];review=reviewed.get(row['id'])
        if review and review['decision']=='verified_subtype':label=reviewed_scope(row,review,sha(project_path(row['cache'])))
        review_rows.append({**row,'label_scope':label})
    results['label_reviewed_validation']=metrics(review_rows,scores[valid],classes)
    reference=previous['results']['exact_label_control']
    for part in ('validation','diagnostic'):
        before=reference[part]['predictions'];after=results[part]['predictions']
        if [(r['id'],r['source_sha256'],r['action_id']) for r in before]!=[(r['id'],r['source_sha256'],r['action_id']) for r in after]:raise ValueError('前后评估输入顺序/标签改变，拒绝比较')
    temporary=target.with_suffix('.npz.part');owned=False
    try:
        with temporary.open('xb') as f:owned=True;np.savez_compressed(f,classes=np.asarray(classes),mean=scaler.mean_,scale=scaler.scale_,coef=model['coef'],intercept=model['intercept'],
            fit_input_identity=json.dumps(identity,sort_keys=True),fit_code_sha256=fit_code,optimization_json=json.dumps(model['optimization'],sort_keys=True),runtime_enabled=False)
        temporary.replace(target)
    finally:
        if owned and temporary.exists():temporary.unlink()
    with np.load(target,allow_pickle=False) as a:
        transformed=standardize_frozen(matrix[valid+diagnostic],a['mean'],a['scale'])
        restored=probabilities(transformed,{'coef':a['coef'],'intercept':a['intercept']})
    np.testing.assert_allclose(restored,scores[valid+diagnostic],atol=1e-12,rtol=0)
    if sha(index)!=FROZEN_INDEX_SHA256 or sha(weight)!=FROZEN_HEAD_SHA256:raise ValueError('拟合期间生产基线改变')
    report={'schema_version':1,'scope':'new partial-view training from one existing source; same previous full33 development and diagnostic evaluation populations; no independent posture accuracy',
        'identity':{'index_sha256':sha(index),'production_weight_sha256':sha(weight),'production_manifest_sha256':sha(manifest),'previous_control_sha256':sha(previous_path),
            'extra_plan_sha256':sha(plan_path),'extra_probe_sha256':sha(probe_path),'training_label_review_sha256':sha(review_path),'trainer_sha256':sha(Path(__file__)),'consumed':consumed},
        'parameters':{'C':.3,'maximum_iterations':200,'minimum_score':.35,'minimum_margin':.05,'new_windows':len(plan['entries']),'new_originals':1,'new_author_groups':1,
            'weighting':'original label / source group / original video; duplicate windows do not multiply source weight','validation_used_in_training':False,
            'uncertain_new_press_windows':sum(r['origin']=='bounded_partial_view_training' and r['label_scope']['specificity']=='family' for r in train_rows),
            'original_generic_curl_supervision_unchanged':True},
        'training':[{k:r[k] for k in ('id','source_sha256','group','action_id','origin','label_scope')}|{'weight':float(w)} for r,w in zip(train_rows,weights)],
        'train_clips':len(training),'validation_clips':len(valid),'diagnostic_clips':len(diagnostic),'results':results,'previous_exact_control':{p:reference[p] for p in ('validation','diagnostic')},
        'optimization':model['optimization'],'fit_code_sha256':fit_code,'verification_reused_fitted_candidate':reuse_candidate,'gate':gate(results['label_reviewed_validation'],model['optimization']),
        'candidate_path':target.relative_to(ROOT).as_posix(),'candidate_sha256':sha(target),'serialization_clips_verified':len(valid)+len(diagnostic),
        'new_pose_inferences_by_training':0,'new_semantic_inferences_by_training':0,'runtime_enabled':False,
        'limitations':['Partial visible exercise labels do not establish full-body variant or technique correctness.','Face-only windows are unobservable, not proof of rest.',
            'The five new windows are training data only; no new independent evaluation people were added.','Scores omit visual-family review and motion guard; not full-pipeline results.']}
    probe_by={r['id']:r for r in probe['rows']}
    report['actual_frame_alignment']=[{'id':r['id'],'reviewed_ms':r['review_actual_ms'],'model_ms':probe_by[r['id']]['sampled_timestamps_ms'],
        'exact':r['review_actual_ms']==probe_by[r['id']]['sampled_timestamps_ms'],
        'maximum_difference_ms':max(abs(a-b) for a,b in zip(r['review_actual_ms'],probe_by[r['id']]['sampled_timestamps_ms']))} for r in plan['entries']]
    report_path=OUT/'partial-view-fusion-training.json'
    if report_path.exists():
        saved=read(report_path)
        if saved['identity']['extra_probe_sha256']==sha(probe_path):report['additional_frame_reviews']=saved.get('additional_frame_reviews',[])
    atomic_json(report_path,report)
    print(json.dumps({'train_clips':len(training),'validation_clips':len(valid),'diagnostic_clips':len(diagnostic),
        'before_correct':reference['validation']['legacy_correct'],'after_correct':results['validation']['legacy_correct'],
        'bench':results['validation']['per_class']['dumbbell_bench_press'],'normal_review':results['label_reviewed_validation']['per_class']['standing_dumbbell_curl'],
        'diagnostic_correct':results['diagnostic']['legacy_correct'],'gate_passed':report['gate']['passed'],'runtime_enabled':False},ensure_ascii=False),flush=True)


if __name__=='__main__':
    if sys.argv[1:]==['--verify-candidate']:main(True)
    elif sys.argv[1:]:raise SystemExit('只支持无参数拟合或--verify-candidate复用已绑定候选验证')
    else:main()
