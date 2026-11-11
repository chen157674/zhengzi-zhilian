"""仅用保存的预测和确切帧复核补充叶类计分，不重训、不改原冻结标签。"""
import hashlib,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment,project_path
configure_environment()
from fitness_app.action_label_policy import reviewed_scope,scope
from fitness_app.research_extract import atomic_json
from scripts.partial_action_training import summarize_predictions,probabilities,standardize_frozen

OUT=ROOT/'data/verification/round3'
def read(path):return json.loads(path.read_text(encoding='utf-8'))
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    report_path=OUT/'partial-label-fusion-training.json';report=read(report_path)
    index=ROOT/'data/verification/step-22/semantic-video-features.json';entries=read(index)['entries']
    frozen={path.replace('\\','/'):value for path,value in report['identity']['frozen_resources'].items()}
    if sha(index)!=frozen[index.relative_to(ROOT).as_posix()]:raise ValueError('冻结索引已经改变')
    if any(sha(project_path(path))!=digest for path,digest in frozen.items()):raise ValueError('生产资源改变，不能沿用本次对照')
    candidate=project_path(report['candidate_path'])
    if sha(candidate)!=report['candidate_sha256']:raise ValueError('离线候选权重改变')
    expected={r['id']:r for r in entries if r['partition']=='validation'}
    review_path=OUT/'curl-validation-subtype-reviews.json';reviewed=read(review_path);frame_path=OUT/'curl-validation-grip-frames.json';frames=read(frame_path)
    if reviewed['frame_manifest_sha256']!=sha(frame_path) or frames['index_sha256']!=sha(index):raise ValueError('实际画面清单或索引改变')
    reviews={r['id']:r for r in reviewed['reviews']};frame_by={r['id']:r for r in frames['rows']}
    generic={r['id'] for r in expected.values() if r['action_id']=='standing_dumbbell_curl'}
    if len(reviews)!=len(reviewed['reviews']) or set(reviews)!=generic or set(frame_by)!=generic:raise ValueError('必须复核全部泛弯举输入，不挑选识别正确样本')
    labels={}
    for key,r in reviews.items():
        entry=expected[key];frame=frame_by[key];digest=sha(project_path(entry['cache']))
        if any(r.get(k)!=frame.get(k) for k in ('source_sha256','start_ms','end_ms','crop','semantic_cache_sha256','semantic_timestamps_ms','actual_timestamps_ms','sheet','row')):raise ValueError('复核与导出的实际画面不一致')
        if digest!=frame['semantic_cache_sha256']:raise ValueError('复核语义缓存改变')
        if r['decision']=='verified_subtype':labels[key]=reviewed_scope(entry,r,digest)
        elif r['decision']=='retain_family_unconfirmed':
            if r.get('independent_error_truth') is not False or not r.get('reason'):raise ValueError('未确认范围缺少依据')
            labels[key]=scope(entry)
        else:raise ValueError('未知复核决策')
    results={}
    for name,value in report['results'].items():
        original=value['validation']['predictions']
        if len(original)!=len(expected) or {r['id'] for r in original}!=set(expected):raise ValueError('保存预测缺失或重复')
        rows=[]
        for prediction in original:
            entry=expected[prediction['id']]
            if any(prediction[k]!=entry[k] for k in ('action_id','group','source_sha256','partition')):raise ValueError('保存预测输入身份改变')
            label=labels.get(entry['id'],scope(entry));rows.append({**prediction,'label_scope':label,'typed_compatible':prediction['accepted'] in label['allowed_actions'],
                'legacy_correct':prediction['accepted']==entry['action_id']})
        classes=sorted(value['validation']['per_class'])+['unknown'];results[name]=summarize_predictions(rows,classes)
        if results[name]['legacy_correct']!=value['validation']['legacy_correct']:raise ValueError('补充标签证据不能改变原标签成绩')
    # 只读回三个实际输入核对序列化后的参数，不重复拟合或整个特征矩阵。
    import numpy as np
    from fitness_app.world_features import WorldFeatures
    from scripts.train_partial_action_fusion import load_features
    plan={r['id']:r for r in read(OUT/'recognition-probe-inputs.json')['entries']};probe={r['id']:r for r in read(OUT/'new-source-action-probe.json')['rows']}
    roundtrip=[];encoder=WorldFeatures()
    with np.load(candidate,allow_pickle=False) as artifact:
        classes=artifact['classes'].tolist();mean=artifact['mean'].copy();scale=artifact['scale'].copy()
        if len(classes)!=34 or mean.shape!=(2736,) or scale.shape!=mean.shape or not np.isfinite(mean).all() or not np.isfinite(scale).all() or np.any(scale<=0):raise ValueError('候选标准化参数无效')
        for key in ('wger-video-17-active','wger-video-48-active','wger-video-78-active'):
            observed=probe[key];feature,_=load_features(plan[key],project_path(observed['semantic_cache']),project_path(observed['pose_cache']),encoder,new=True)
            scaled=standardize_frozen(feature[None],mean,scale)
            for prefix,name in [('control','exact_label_control'),('partial','partial_label_candidate')]:
                model={'coef':artifact[prefix+'_coef'],'intercept':artifact[prefix+'_intercept']}
                values=probabilities(scaled,model)[0];order=np.argsort(values)[::-1];score=float(values[order[0]]);gap=score-float(values[order[1]])
                top=classes[order[0]];accepted=top if top!='unknown' and score>=.35 and gap>=.05 else None
                saved=next(r for r in report['results'][name]['diagnostic']['predictions'] if r['id']==key)
                if top!=saved['top'] or accepted!=saved['accepted'] or abs(score-saved['score'])>1e-6 or abs(gap-saved['margin'])>1e-6:raise ValueError('候选保存前后预测不同')
                roundtrip.append({'id':key,'candidate':name,'top':top,'accepted':accepted,'score_error':abs(score-saved['score'])})
    value={'schema_version':1,'scope':'same saved full33 fusion-head predictions; developer-reviewed label specificity only, not independently annotated test or technique validation',
        'training_report_sha256':sha(report_path),'review_sha256':sha(review_path),'frame_manifest_sha256':sha(frame_path),'script_sha256':sha(Path(__file__)),
        'helper_sha256':sha(ROOT/'scripts/partial_action_training.py'),'confirmed_normal_windows':sum(v['specificity']=='leaf' for v in labels.values()),
        'unconfirmed_family_windows':sum(v['specificity']=='family' for v in labels.values()),'results':results,
        'training_or_predictions_changed':False,'new_pose_inferences':0,'new_video_semantic_inferences':0,'new_training_runs':0,'artifact_roundtrip_cases':roundtrip,'runtime_enabled':False,
        'limitations':['Developer had already inspected candidate results; omitted scores in contact sheets do not constitute an independent blind review.',
            'All 24 original generic-curl windows remain in legacy denominators; four unconfirmed windows were not removed or relabeled as background.',
            'Exclusive class labels and developer grip review do not establish independent posture-error truth.']}
    atomic_json(OUT/'partial-label-fusion-label-audit.json',value)
    print(json.dumps({'confirmed_normal':value['confirmed_normal_windows'],'unconfirmed_family':value['unconfirmed_family_windows'],
        'normal':{k:v['per_class']['standing_dumbbell_curl'] for k,v in results.items()},'new_training_runs':0,'runtime_enabled':False},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
