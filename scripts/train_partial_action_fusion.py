"""复用已冻结视频特征训练33类部分标签候选；不写生产权重、不调用GPU。"""
import hashlib,json,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment,project_path
configure_environment()
import joblib,numpy as np
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_limits
from fitness_app.semantic_features import feature_blocks
from fitness_app.world_features import WorldFeatures
from fitness_app.action_label_policy import scope,reviewed_scope
from fitness_app.research_extract import atomic_json
from scripts.train_curl_subtype import vector
from scripts.partial_action_training import assert_source_split,source_weights,allowed_mask,fit_linear,probabilities,metrics

OLD=ROOT/'data/verification/step-22';OUT=ROOT/'data/verification/round3'
FROZEN_INDEX_SHA256='0b99c418d52715f7bf52dc716775d0197a59d6389edf86b6538bc4d9cfc5d087'
FROZEN_HEAD_SHA256='1807e0799465e0eb62bebbf2681225602728d161528151cffe9f3461c373ca30'


def read(path):return json.loads(path.read_text(encoding='utf-8'))
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def load_features(source,semantic,pose,encoder,expected=None,new=False):
    vector(semantic,source)  # 校验原片、窗口、裁剪、编码策略及真实向量，不把标签送入编码。
    with np.load(semantic,allow_pickle=False) as a:
        embedding=a['embedding'].copy();prediction=json.loads(str(a['prediction']));times=a['timestamps'].copy();identity=json.loads(str(a['identity']))
    if times.shape!=(8,) or not np.isfinite(times).all() or np.any(np.diff(times)<=0) or times[0]<source['start_ms'] or times[-1]>=source['end_ms']:
        raise ValueError('视频语义时间窗无效')
    if new and identity.get('pose_sha256')!=sha(pose):raise ValueError('新增来源的姿态与语义缓存不匹配')
    with np.load(pose,allow_pickle=False) as a:
        pose_identity=json.loads(str(a['identity']))
        if any(pose_identity.get(k)!=source.get(k) for k in ('source_sha256','start_ms','end_ms','crop')):raise ValueError('世界坐标缓存输入身份不一致')
        if new and str(a['signature'])!=hashlib.sha256(json.dumps(pose_identity,sort_keys=True).encode()).hexdigest():raise ValueError('新增姿态缓存签名不一致')
        world,visibility=encoder.extract({k:a[k].copy() for k in ('timestamps','world','people')},times[0],times[-1])
    value=feature_blocks(embedding,prediction,world,visibility)['full'];digest=hashlib.sha256(value.tobytes()).hexdigest()
    if expected and digest!=expected['feature_sha256']:raise ValueError('与原始训练消费的完整融合特征不同: '+source['id'])
    return value,{'id':source['id'],'source_sha256':source['source_sha256'],'semantic_cache_sha256':sha(semantic),
        'pose_cache_sha256':sha(pose),'feature_sha256':digest,'original_feature_hash_verified':expected is not None}


def gate(result,optimization):
    failures=[]
    if not optimization['stationarity_passed']:failures.append('训练目标未达到预设梯度收敛线')
    for action,v in result['per_class'].items():
        if v['verified_leaf_recall'] is None or v['verified_leaf_recall']<.8:failures.append(action+': 已确认叶类召回不足80%或缺少标签')
        if v['verified_leaf_clips']<3 or v['verified_leaf_groups']<2:failures.append(action+': 逐类来源覆盖不足')
    if result['negative_false_accepts']:failures.append('目录外负例被接纳')
    failures.append('仅融合头离线对照，完整复核/运动保护链路和独立具体纠错尚未验收')
    return {'passed':not failures,'failures':failures,'runtime_enabled':False}


def main():
    started=time.monotonic();index=OLD/'semantic-video-features.json';weight=ROOT/'data/models/semantic-action-classifier.joblib';manifest=ROOT/'data/models/semantic-action-classifier.json'
    if sha(index)!=FROZEN_INDEX_SHA256 or sha(weight)!=FROZEN_HEAD_SHA256:raise ValueError('冻结原索引或生产权重改变，拒绝悄悄切换基线')
    production_hashes={str(p.relative_to(ROOT)):sha(p) for p in (index,weight,manifest)}
    original=read(index);consumed=read(OLD/'semantic-fusion-training.json')['identity']['consumed'];expected={r['id']:r for r in consumed}
    if len(expected)!=len(consumed) or set(expected)!={r['id'] for r in original['entries']}:raise ValueError('原特征消费清单不完整')
    reviews=read(OUT/'curl-subtype-reviews.json')['reviews'];review_by={r['id']:r for r in reviews};reviewed=set()
    if len(review_by)!=len(reviews):raise ValueError('标签复核身份重复')
    plan_path=OUT/'recognition-probe-inputs.json';probe_path=OUT/'new-source-action-probe.json';plan=read(plan_path);probe=read(probe_path)
    if probe['status']!='completed' or probe['plan_sha256']!=sha(plan_path):raise ValueError('新增来源缓存未完整绑定原计划')
    observed={r['id']:r for r in probe['rows']}
    if len(observed)!=len(probe['rows']) or set(observed)!={r['id'] for r in plan['entries']}:raise ValueError('新增特征消费清单不完整')
    encoder=WorldFeatures();rows=[];features=[];identities=[];excluded=[]
    for i,source in enumerate(original['entries']):
        semantic=project_path(source['cache']);label=scope(source)
        if source['id'] in review_by:
            label=reviewed_scope(source,review_by[source['id']],sha(semantic));reviewed.add(source['id'])
        if label.get('training_excluded'):
            if source['partition']!='train':raise ValueError('不能排除冻结的原验证样本')
            excluded.append({'id':source['id'],'source_sha256':source['source_sha256'],'reason':label['reason']});continue
        exp=expected[source['id']]
        if any(exp[k]!=source[k] for k in ('id','source_sha256','partition')) or exp['action']!=source['action_id']:raise ValueError('冻结原标签或分区不匹配')
        value,identity=load_features(source,semantic,OLD/(source['id']+'-full-world-features.npz'),encoder,expected=exp)
        rows.append({**source,'origin':'frozen_original','label_scope':label,'control_label':label['leaf_label'] or source['action_id']})
        features.append(value);identities.append(identity)
        if (i+1)%200==0:print(json.dumps({'stage':'verified_frozen_features','inputs':i+1}),flush=True)
    if reviewed!=set(review_by):raise ValueError('复核清单存在未使用输入')
    for source in plan['entries']:
        observed_row=observed[source['id']];label=scope(source)
        if any(observed_row[k]!=source[k] for k in ('source_sha256','partition','group')) or observed_row['expected']!=source['action_id']:raise ValueError('新增缓存标签或分区变化')
        value,identity=load_features(source,project_path(observed_row['semantic_cache']),project_path(observed_row['pose_cache']),encoder,new=True)
        rows.append({**source,'origin':'new_cached_source','label_scope':label,'control_label':label['leaf_label'] or source['action_id']})
        features.append(value);identities.append(identity)
    if len({r['id'] for r in rows})!=len(rows):raise ValueError('输入身份重复')
    train=np.asarray([i for i,r in enumerate(rows) if r['partition']=='train']);evaluation=np.asarray([i for i,r in enumerate(rows) if r['partition']!='train'])
    training=[rows[i] for i in train];validation=[rows[i] for i in evaluation]
    assert_source_split(training,validation)
    production=joblib.load(weight);classes=list(production.classes_)
    if len(classes)!=34 or set(r['control_label'] for r in training)!=set(classes):raise ValueError('33动作及目录外类别训练覆盖不全')
    matrix=np.asarray(features,np.float32);scaler=StandardScaler().fit(matrix[train]);scaled=scaler.transform(matrix);weights=source_weights(training)
    candidates={};fitted={}
    with threadpool_limits(limits=4):
        for name,partial in [('exact_label_control',False),('partial_label_candidate',True)]:
            print(json.dumps({'stage':'fit_cpu','candidate':name,'train':len(train),'ambiguous_family_inputs':sum(r['label_scope']['specificity']=='family' for r in training)}),flush=True)
            fitted[name]=fit_linear(scaled[train],allowed_mask(training,classes,partial),weights)
            candidates[name]=probabilities(scaled[evaluation],fitted[name])
        candidates['production_head']=production.predict_proba(matrix[evaluation])
    partitions={name:np.asarray([i for i,r in enumerate(validation) if r['partition']==name],int) for name in ('validation','diagnostic')}
    results={}
    for name,scores in candidates.items():
        results[name]={partition:metrics([validation[i] for i in indices],scores[indices],classes) for partition,indices in partitions.items()}
        if name in fitted:results[name]['optimization']=fitted[name]['optimization']
    if any(sha(project_path(path))!=value for path,value in production_hashes.items()):raise ValueError('运行期间生产资源改变，不保存候选')
    target=OUT/'partial-label-fusion-candidates.npz';partial_path=target.with_suffix('.npz.part');owned=False
    try:
        with partial_path.open('xb') as f:
            owned=True
            np.savez_compressed(f,classes=np.asarray(classes),mean=scaler.mean_,scale=scaler.scale_,
                control_coef=fitted['exact_label_control']['coef'],control_intercept=fitted['exact_label_control']['intercept'],
                partial_coef=fitted['partial_label_candidate']['coef'],partial_intercept=fitted['partial_label_candidate']['intercept'])
        partial_path.replace(target)
    finally:
        if owned and partial_path.exists():partial_path.unlink()
    report={'schema_version':1,'scope':'source-disjoint development and previously observed diagnostic inputs; frozen full2736 video/pose features; not an independent test',
        'method':'weighted allowed-set marginal cross entropy plus fixed L2; a generic curl label permits normal or hammer and supplies no leaf truth',
        'parameters':{'C':.3,'maximum_iterations':200,'minimum_score':.35,'minimum_margin':.05,'weighting':'original label / source group / original video; same weights and scaler for both losses',
            'initialization':'all-zero coefficients/intercepts; no validation input or ground-truth token in model features','thresholds_selected_on_these_results':False},
        'identity':{'frozen_resources':production_hashes,'original_training_report_sha256':sha(OLD/'semantic-fusion-training.json'),'plan_sha256':sha(plan_path),'probe_sha256':sha(probe_path),
            'reviews_sha256':sha(OUT/'curl-subtype-reviews.json'),'trainer_sha256':sha(Path(__file__)),'helper_sha256':sha(ROOT/'scripts/partial_action_training.py'),'consumed':identities},
        'training':[{k:r[k] for k in ('id','source_sha256','group','action_id','control_label','label_scope','origin')}|{'weight':float(w)} for r,w in zip(training,weights)],
        'excluded_training':excluded,'train_clips':len(training),'original_validation_clips':len(partitions['validation']),'diagnostic_clips':len(partitions['diagnostic']),
        'results':results,'candidate_path':target.relative_to(ROOT).as_posix(),'candidate_sha256':sha(target),
        'gate':gate(results['partial_label_candidate']['validation'],fitted['partial_label_candidate']['optimization']),
        'runtime_enabled':False,'production_resources_unchanged':True,'new_pose_inferences':0,'new_video_semantic_inferences':0,'test_used':False,
        'elapsed_seconds':round(time.monotonic()-started,3),
        'limitations':['Family-compatible predictions are not verified leaf correctness.','Frozen validation has already informed prior development, and diagnostics were previously inspected.',
            'Fusion head scores omit production visual-family review and motion guard, so they are not comparable to the published full-pipeline 171/193 figure.','No independent posture-error labels, real-phone or paid-provider acceptance from this run.']}
    atomic_json(OUT/'partial-label-fusion-training.json',report)
    print(json.dumps({'stage':'completed','train_clips':len(training),'original_validation_clips':len(partitions['validation']),'diagnostic_clips':len(partitions['diagnostic']),
        'scores':{name:{p:{k:v for k,v in value.items() if k not in ('per_class','predictions')} for p,value in result.items() if p in partitions} for name,result in results.items()},
        'optimization':{name:value['optimization'] for name,value in fitted.items()},'gate_passed':report['gate']['passed'],'runtime_enabled':False,'elapsed_seconds':report['elapsed_seconds']},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
