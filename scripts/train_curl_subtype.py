"""仅训练握法子类候选；泛弯举保留动作族，诊断来源不回灌训练，不发布。"""
import hashlib,json,sys
from pathlib import Path
from collections import Counter
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment,project_path
configure_environment()
import joblib,numpy as np
from sklearn.linear_model import LogisticRegression
from threadpoolctl import threadpool_limits
from fitness_app.action_label_policy import scope,reviewed_scope,CURL_ACTIONS,VERSION
from fitness_app.research_extract import atomic_json

OUT=ROOT/'data/verification/round3'


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def vector(path,entry):
    with np.load(path,allow_pickle=False) as a:
        identity=json.loads(str(a['identity']));signature=str(a['signature']);value=np.asarray(a['embedding'],np.float32).copy()
    if hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()!=signature or entry.get('feature_signature',signature)!=signature:raise ValueError('视频语义缓存签名不一致')
    if any(identity.get(k)!=entry.get(k) for k in ('source_sha256','start_ms','end_ms','crop')):raise ValueError('视频语义缓存属于其他原片/窗口/裁剪')
    policy=identity.get('encoder_policy_sha256') or identity.get('policy_sha256')
    if policy!=json.loads((ROOT/'data/models/semantic-action-classifier.json').read_text(encoding='utf-8'))['encoder_policy_sha256']:raise ValueError('视频语义编码策略与冻结基线不同')
    if value.shape!=(2560,) or not np.isfinite(value).all() or np.linalg.norm(value)<1e-8:raise ValueError('候选语义特征无效')
    return value/np.linalg.norm(value)


def main():
    original=ROOT/'data/verification/step-22/semantic-video-features.json';plan=OUT/'recognition-probe-inputs.json';probe=OUT/'new-source-action-probe.json';old=json.loads(original.read_text(encoding='utf-8'));new=json.loads(plan.read_text(encoding='utf-8'));observed=json.loads(probe.read_text(encoding='utf-8'))
    if observed['status']!='completed' or observed['plan_sha256']!=digest(plan):raise ValueError('新来源特征未完整冻结')
    review_path=OUT/'curl-subtype-reviews.json';reviews=json.loads(review_path.read_text(encoding='utf-8'))['reviews'] if review_path.exists() else []
    if len({r['id'] for r in reviews})!=len(reviews):raise ValueError('复核输入身份重复')
    reviewed={r['id']:r for r in reviews};used=set();by={r['id']:r for r in observed['rows']};rows=[];excluded=[]
    for source in old['entries']:
        if source['action_id'] not in CURL_ACTIONS:continue
        label=scope(source)
        if source['id'] in reviewed:
            label=reviewed_scope(source,reviewed[source['id']],digest(project_path(source['cache'])));used.add(source['id'])
        if label['leaf_label'] is None:excluded.append({k:source[k] for k in ('id','action_id','partition','group','source_sha256')}|label);continue
        rows.append({**source,'leaf_label':label['leaf_label'],'origin':'bounded_developer_subtype_review' if source['id'] in used else 'original_specific_subtype','vector':vector(project_path(source['cache']),source)})
    if used!=set(reviewed):raise ValueError('存在未匹配原输入的握法复核')
    for source in new['entries']:
        if source['action_id'] not in CURL_ACTIONS:continue
        label=scope(source);row=by[source['id']]
        rows.append({**source,'leaf_label':label['leaf_label'],'origin':'new_source','vector':vector(project_path(row['semantic_cache']),source)})
    training=[r for r in rows if r['partition']=='train'];evaluation=[r for r in rows if r['partition']!='train']
    if {r['leaf_label'] for r in training}!=set(CURL_ACTIONS):raise ValueError('握法训练叶类不完整')
    if {r['group'] for r in training}&{r['group'] for r in evaluation}:raise ValueError('候选训练/评估作者组交叉')
    counts=Counter((r['leaf_label'],r['source_sha256']) for r in training);originals=Counter(a for a,_ in counts)
    weights=np.asarray([1/(counts[(r['leaf_label'],r['source_sha256'])]*originals[r['leaf_label']]) for r in training]);weights*=len(weights)/weights.sum()
    matrix=np.asarray([r['vector'] for r in training]);labels=[r['leaf_label'] for r in training]
    # 固定既有方向语义基线的量级，不用已观测诊断集选择C或拒判阈值。
    with threadpool_limits(limits=4):model=LogisticRegression(C=300.,max_iter=2000,random_state=20261024).fit(matrix,labels,sample_weight=weights)
    results=[]
    for row in evaluation:
        probabilities=model.predict_proba(row['vector'][None])[0];order=np.argsort(probabilities)[::-1];top=str(model.classes_[order[0]]);score=float(probabilities[order[0]]);margin=score-float(probabilities[order[1]])
        results.append({k:row[k] for k in ('id','partition','group','source_sha256')}|{'expected':row['leaf_label'],'top':top,'accepted':top if score>=.8 and margin>=.25 else None,'score':score,'margin':margin})
    class_counts={a:{'train_clips':sum(r['leaf_label']==a for r in training),'train_originals':len({r['source_sha256'] for r in training if r['leaf_label']==a}),'train_groups':len({r['group'] for r in training if r['leaf_label']==a}),
        'eval_clips':sum(r['expected']==a for r in results),'eval_groups':len({r['group'] for r in results if r['expected']==a}),'eval_accepted_correct':sum(r['expected']==a and r['accepted']==a for r in results)} for a in CURL_ACTIONS}
    for a,value in class_counts.items():value['eval_recall']=value['eval_accepted_correct']/value['eval_clips'] if value['eval_clips'] else None
    gate=all(v['train_groups']>=2 and v['eval_clips']>=3 and v['eval_groups']>=2 and (v['eval_recall'] or 0)>=.8 for v in class_counts.values())
    target=OUT/'curl-subtype-candidate.joblib';partial=target.with_suffix('.joblib.part');owned=False
    try:
        with partial.open('xb') as f:owned=True;joblib.dump(model,f)
        partial.replace(target)
    finally:
        if owned and partial.exists():partial.unlink()
    previous_path=OUT/'curl-subtype-training.json';previous=json.loads(previous_path.read_text(encoding='utf-8')) if previous_path.exists() else None
    baseline=previous.get('initial_candidate') if previous else None
    if baseline is None and previous:baseline={k:previous[k] for k in ('identity','fixed_parameters','per_class','evaluation','candidate_sha256','development_gate_passed')}
    report={'schema_version':2,'scope':'experimental subtype head on frozen continuous-video embeddings; not full33 or independent error validation',
        'label_policy':VERSION,'fixed_parameters':{'C':300.,'minimum_score':.8,'minimum_margin':.25},'identity':{'original_features_sha256':digest(original),'new_plan_sha256':digest(plan),'probe_sha256':digest(probe),'script_sha256':digest(Path(__file__))},
        'training':[{k:r[k] for k in ('id','leaf_label','group','source_sha256','origin')} for r in training],'excluded_generic_leaves':excluded,'per_class':class_counts,
        'review_sha256':digest(review_path) if review_path.exists() else None,'reviewed_original_inputs':sorted(used),'initial_candidate':baseline,
        'evaluation':results,'candidate_path':target.relative_to(ROOT).as_posix(),'candidate_sha256':digest(target),'development_gate_passed':gate,'runtime_enabled':False,
        'release_note':'One-source or one-author results do not meet release quality; production weights unchanged. Wger is evaluation-only, never in training.',
        'new_gpu_inferences':0,'specific_error_validation_passed':False}
    atomic_json(OUT/'curl-subtype-training.json',report)
    print(json.dumps({'training':len(training),'excluded_generic':len(excluded),'evaluation':len(results),'per_class':class_counts,'development_gate_passed':gate,'runtime_enabled':False},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
