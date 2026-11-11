"""用已冻结的连续视频/骨架特征学习GB/PB片段条件；不是逐帧圆背纠错发布器。"""
import hashlib,json,sys
from collections import Counter
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment,project_path
configure_environment()
import joblib,numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_limits
from fitness_app.research_data import load_dataset,DL_FOLDS
from fitness_app.research_extract import atomic_json
from fitness_app.world_features import WorldFeatures
from scripts.train_partial_action_fusion import load_features,FROZEN_INDEX_SHA256,FROZEN_HEAD_SHA256
from scripts.partial_action_training import source_weights,assert_source_split

OLD=ROOT/'data/verification/step-22';OUT=ROOT/'data/verification/round3'
METHODS=(('semantic_direction_C300',slice(0,2560),300.,False),('geometry_linear_C0.3',slice(2594,2736),.3,True),('full_linear_C0.3',slice(0,2736),.3,True))


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def read(path):return json.loads(path.read_text(encoding='utf-8'))


def cases_for(entries,samples):
    """标签来自冻结研究索引，不根据GB/PB文件名或分类器预测反推。"""
    known={s.sha256:s for s in samples if s.dataset_id=='w5prmmxyt9'};rows=[]
    for entry in entries:
        if not entry['path'].startswith('data/datasets/mydeadlift/'):continue
        sample=known.get(entry['source_sha256'])
        if sample is None or sample.partition!='development' or sample.annotation_status!='consistent_source_label' or sample.quality_use!='source_GB_PB_only':raise ValueError('条件训练仅允许原开发分区的无冲突来源标签')
        if sample.label not in {'GB','PB'} or entry['action_id']!='conventional_deadlift' or entry['path']!='data/datasets/'+sample.path or entry['group']!='w5prmmxyt9:'+sample.subject_group or entry['crop'] is not None:
            raise ValueError('条件标签与实际来源、人物或裁剪不一致')
        if type(sample.validation_fold) is not int or not 0<=sample.validation_fold<4 or sample.subject_group not in DL_FOLDS[sample.validation_fold]:raise ValueError('条件训练人物划分变化')
        rows.append({**entry,'source_label':sample.label,'source_subject':sample.subject_group,'condition_fold':sample.validation_fold})
    if len({r['source_sha256'] for r in rows})!=len(rows) or len({r['id'] for r in rows})!=len(rows):raise ValueError('重复裁剪或原片不能增加条件样本数')
    return rows


def condition_metrics(rows):
    if any(r.get('source_label') not in {'GB','PB'} or r.get('accepted') not in {None,'GB','PB'} for r in rows):raise ValueError('条件评价出现未知标签')
    result={'clips':len(rows),'accepted':sum(r['accepted'] is not None for r in rows),'correct':sum(r['accepted']==r['source_label'] for r in rows)}
    result['accuracy_including_rejections']=result['correct']/len(rows) if rows else None
    result['coverage']=result['accepted']/len(rows) if rows else None
    result['per_condition']={}
    for label in ('GB','PB'):
        cases=[r for r in rows if r['source_label']==label];accepted=[r for r in rows if r['accepted']==label];correct=sum(r['accepted']==label for r in cases)
        result['per_condition'][label]={'clips':len(cases),'correct':correct,'rejected':sum(r['accepted'] is None for r in cases),
            'recall_including_rejections':correct/len(cases) if cases else None,'precision':correct/len(accepted) if accepted else None}
    result['balanced_accuracy']=sum(v['recall_including_rejections'] or 0 for v in result['per_condition'].values())/2
    return result


def decision(values,classes,minimum=.8):
    if len(classes)!=2 or set(classes)!={'GB','PB'} or np.asarray(values).shape!=(2,) or not np.isfinite(values).all() or np.any(values<0) or not np.isclose(np.sum(values),1.,atol=1e-6):raise ValueError('条件概率不合法')
    top=int(np.argmax(values));score=float(values[top]);return str(classes[top]),str(classes[top]) if score>=minimum else None,score


def release_gate(metrics,folds):
    reasons=[]
    for prefix,result in [('all',metrics)]+[(str(r['fold']),r['metrics']) for r in folds]:
        for label,minimum in (('GB',.9),('PB',.8)):
            recall=result['per_condition'][label]['recall_including_rejections']
            if recall is None or recall<minimum:reasons.append(prefix+': '+label+' recall below '+str(minimum))
        if result['coverage'] is None or result['coverage']<.8:reasons.append(prefix+': coverage below 0.8')
    if (metrics['per_condition']['PB']['precision'] or 0)<.85:reasons.append('PB precision below 0.85')
    return {'development_thresholds_passed':not reasons,'failures':reasons,'runtime_enabled':False,
        'release_gate_passed':False,'release_pending':['whole-clip source labels do not establish exact-window or error-onset truth','independent external sources and supported views','error body region and instruction validation','real-time window behavior and existing rules integration']}


def window_observability(cases,identities):
    """只核对8帧窗口是否看到俯身阶段；髋角与肩髋方向不构成圆背真值。"""
    import math
    from fitness_app.upstream import calculate_angle
    expected={r['id']:r for r in identities};rows=[]
    for case in cases:
        semantic=project_path(case['cache']);pose=OLD/(case['id']+'-full-world-features.npz')
        if sha(semantic)!=expected[case['id']]['semantic_cache_sha256'] or sha(pose)!=expected[case['id']]['pose_cache_sha256']:raise ValueError('窗口审计缓存与拟合输入不同')
        with np.load(semantic,allow_pickle=False) as a:desired=a['timestamps'].copy()
        with np.load(pose,allow_pickle=False) as a:times=a['timestamps'].copy();points=a['points'].copy();people=a['people'].copy();sizes=a['sizes'].copy()
        observed=[]
        for stamp in desired:
            i=int(np.argmin(abs(times-stamp)))
            if abs(float(times[i]-stamp))>100 or people[i]!=1:continue
            valid=[]
            for offset in (0,1):
                joints=points[i,[11+offset,23+offset,25+offset]]
                if not np.isfinite(joints).all() or np.min(joints[:,3:5])<.5:continue
                shoulder,hip,knee=joints[:,:2]*sizes[i]
                try:angle=calculate_angle(shoulder,hip,knee)
                except ValueError:continue
                vector=shoulder-hip;length=float(np.linalg.norm(vector))
                if length<1e-6:continue
                tilt=math.degrees(math.acos(float(np.clip(-vector[1]/length,-1,1))))
                valid.append((float(np.min(joints[:,3:5])),angle,tilt))
            if valid:
                _,angle,tilt=max(valid);observed.append({'hip_image_degrees':angle,'trunk_tilt_degrees':tilt})
        bent=sum(r['hip_image_degrees']<150 and r['trunk_tilt_degrees']>20 for r in observed)
        state='insufficient_joint_coverage' if len(observed)<6 else 'hinge_stage_visible' if bent>=2 else 'hinge_stage_not_established'
        rows.append({'id':case['id'],'source_label':case['source_label'],'source_subject':case['source_subject'],'matched_joint_frames':len(observed),'hinge_frames':bent,'state':state})
    return {'scope':'label-independent cached joint observability; no spine points or new RGB inference','policy':{'maximum_alignment_ms':100,'minimum_joint_frames':6,'minimum_hinge_frames':2,'hip_below_degrees':150,'trunk_tilt_above_degrees':20,
        'threshold_meaning':'engineering stage coverage, never an error threshold'},
        'by_condition':{label:dict(Counter(r['state'] for r in rows if r['source_label']==label)) for label in ('GB','PB')},'rows':rows,'new_training_runs':0}


def audit_windows():
    path=OUT/'deadlift-condition-training.json';report=read(path);index=OLD/'semantic-video-features.json'
    if sha(index)!=report['identity']['frozen_index_sha256']:raise ValueError('原索引已改变')
    if sha(project_path(report['candidate_path']))!=report['candidate_sha256']:raise ValueError('候选参数已改变')
    cases=cases_for(read(index)['entries'],load_dataset())
    if {r['id'] for r in cases}!={r['id'] for r in report['identity']['consumed']}:raise ValueError('原拟合清单已改变')
    report['final_fit_ids']=[r['id'] for r in cases]
    report['window_audit']=window_observability(cases,report['identity']['consumed'])
    report['window_audit']['script_sha256']=sha(Path(__file__))
    atomic_json(path,report)
    print(json.dumps({'window_audit':report['window_audit']['by_condition'],'new_training_runs':0},ensure_ascii=False),flush=True)


def main():
    index=OLD/'semantic-video-features.json';weight=ROOT/'data/models/semantic-action-classifier.joblib'
    if sha(index)!=FROZEN_INDEX_SHA256 or sha(weight)!=FROZEN_HEAD_SHA256:raise ValueError('冻结识别基线已经变化')
    raw=read(index);samples=load_dataset();cases=cases_for(raw['entries'],samples)
    development=[s for s in samples if s.dataset_id=='w5prmmxyt9' and s.partition=='development']
    if {r['source_sha256'] for r in cases}!={s.sha256 for s in development}:raise ValueError('不能挑选已有条件特征，必须包含全部原开发输入')
    consumed={r['id']:r for r in read(OLD/'semantic-fusion-training.json')['identity']['consumed']}
    encoder=WorldFeatures();features=[];identities=[]
    for case in cases:
        if sha(project_path(case['path']))!=case['source_sha256']:raise ValueError('原媒体内容改变')
        value,identity=load_features(case,project_path(case['cache']),OLD/(case['id']+'-full-world-features.npz'),encoder,expected=consumed[case['id']])
        features.append(value);identities.append(identity)
    matrix=np.asarray(features,np.float32);results=[]
    with threadpool_limits(limits=4):
        for name,columns,strength,scaled in METHODS:
            predictions=[];folds=[]
            for fold in range(4):
                ti=[i for i,r in enumerate(cases) if r['condition_fold']!=fold];vi=[i for i,r in enumerate(cases) if r['condition_fold']==fold]
                training=[{**cases[i],'partition':'train'} for i in ti];validation=[{**cases[i],'partition':'validation'} for i in vi]
                assert_source_split(training,validation)
                weights=source_weights([{**r,'action_id':r['source_label']} for r in training]);labels=[r['source_label'] for r in training]
                if set(labels)!={'GB','PB'} or {r['source_label'] for r in validation}!={'GB','PB'}:raise ValueError('每个固定折必须覆盖两个来源条件')
                linear=LogisticRegression(C=strength,max_iter=2000,random_state=20261024);model=make_pipeline(StandardScaler(),linear) if scaled else linear
                model.fit(matrix[ti,columns],labels,**({'logisticregression__sample_weight':weights} if scaled else {'sample_weight':weights}))
                scores=model.predict_proba(matrix[vi,columns]);local=[]
                for case,values in zip(validation,scores):
                    top,accepted,score=decision(values,list(model.classes_));local.append({k:case[k] for k in ('id','source_sha256','source_subject','source_label','start_ms','end_ms')}|{
                        'fold':fold,'top':top,'accepted':accepted,'score':score,'label_scope':'publisher whole-clip GB/PB condition, not exact-window error onset'})
                predictions.extend(local);folds.append({'fold':fold,'training_subjects':sorted({r['source_subject'] for r in training}),'evaluation_subjects':sorted({r['source_subject'] for r in validation}),
                    'training_ids':[r['id'] for r in training],'evaluation_ids':[r['id'] for r in validation],'metrics':condition_metrics(local),'iterations':linear.n_iter_.tolist()})
            if len(predictions)!=len(cases) or {r['id'] for r in predictions}!={r['id'] for r in cases}:raise ValueError('折外预测缺失，不能缩小分母')
            metrics=condition_metrics(predictions);gate=release_gate(metrics,folds);results.append({'name':name,'metrics':metrics,'folds':folds,'predictions':predictions,'gate':gate})
    selected=max(results,key=lambda r:(r['gate']['development_thresholds_passed'],sum(f['metrics']['balanced_accuracy'] for f in r['folds'])/4,r['metrics']['per_condition']['PB']['precision'] or 0))
    spec=next(r for r in METHODS if r[0]==selected['name']);name,columns,strength,scaled=spec
    linear=LogisticRegression(C=strength,max_iter=2000,random_state=20261024);model=make_pipeline(StandardScaler(),linear) if scaled else linear
    weights=source_weights([{**r,'action_id':r['source_label']} for r in cases])
    with threadpool_limits(limits=4):model.fit(matrix[:,columns],[r['source_label'] for r in cases],**({'logisticregression__sample_weight':weights} if scaled else {'sample_weight':weights}))
    if sha(index)!=FROZEN_INDEX_SHA256 or sha(weight)!=FROZEN_HEAD_SHA256:raise ValueError('运行期间生产基线改变')
    target=OUT/'deadlift-condition-candidate.joblib';temporary=target.with_suffix('.joblib.part');owned=False
    try:
        with temporary.open('xb') as f:owned=True;joblib.dump({'model':model,'columns':(columns.start,columns.stop),'runtime_enabled':False,'scope':'GB/PB source-clip condition only'},f,compress=3)
        temporary.replace(target)
    finally:
        if owned and temporary.exists():temporary.unlink()
    restored=joblib.load(target)
    with threadpool_limits(limits=4):np.testing.assert_allclose(restored['model'].predict_proba(matrix[:,slice(*restored['columns'])]),model.predict_proba(matrix[:,columns]),rtol=0,atol=1e-12)
    value={'schema_version':1,'scope':'subject-separated development comparison for publisher GB/PB clip conditions using previously frozen video embeddings; no per-frame curvature/localization truth',
        'source':{'dataset_id':'w5prmmxyt9','doi':'10.17632/w5prmmxyt9.1','license':'CC BY 4.0','attribution':'Ivan Conanta, Gloria Virginia','labels':'unmodified research index GB/PB; no invented extension labels'},
        'input_count':len(cases),'labels':dict(Counter(r['source_label'] for r in cases)),'subjects':sorted({r['source_subject'] for r in cases}),
        'identity':{'frozen_index_sha256':sha(index),'production_head_sha256':sha(weight),'original_training_report_sha256':sha(OLD/'semantic-fusion-training.json'),
            'script_sha256':sha(Path(__file__)),'feature_loader_sha256':sha(ROOT/'scripts/train_partial_action_fusion.py'),'consumed':identities},
        'fixed_parameters':{'minimum_score':.8,'folds':[list(x) for x in DL_FOLDS],'weighting':'source condition / source group / original video','thresholds_tuned_on_these_results':False},
        'methods':results,'selected_reference':selected['name'],'final_fit_ids':[r['id'] for r in cases],
        'candidate_path':target.relative_to(ROOT).as_posix(),'candidate_sha256':sha(target),'gate':selected['gate'],
        'window_audit':window_observability(cases,identities),
        'serialization_clips_verified':len(cases),'new_pose_inferences':0,'new_semantic_video_inferences':0,'runtime_enabled':False,'historical_test_used_by_this_run':False,
        'limitations':['The original research test was used in historical experiments; it is not an untouched independent set. This run does not read its media or features.',
            'All three methods use identical 97 inputs and folds; older methods with different coverage are not directly comparable.',
            'A clip label does not establish whether the middle 8-frame window contains visible curvature.','GB never means the whole movement is correct or safe.',
            'No personal data, camera, DeepSeek or front-end changes in this run.']}
    atomic_json(OUT/'deadlift-condition-training.json',value)
    print(json.dumps({'clips':len(cases),'subjects':value['subjects'],'methods':{r['name']:r['metrics'] for r in results},'selected':selected['name'],
        'development_thresholds_passed':selected['gate']['development_thresholds_passed'],'runtime_enabled':False},ensure_ascii=False),flush=True)


if __name__=='__main__':
    if sys.argv[1:]==['--audit-windows']:audit_windows()
    elif sys.argv[1:]:raise SystemExit('只支持无参数训练或--audit-windows只读缓存审计')
    else:main()
