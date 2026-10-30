"""整段GB/PB时序候选；不注册产品接口，不把来源类别改称逐帧脊柱真值。"""
import hashlib
import json
import numpy as np
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVC

from .paths import ROOT,project_path
from .research_data import SEED,load_dataset
from .research_features import frame_angles
from .research_metrics import evaluate
from .research_extract import atomic_json

CONFIG={'schema':'deadlift-ordered-angles-1','columns':['hip','knee','trunk_vertical'],
        'bins':32,'minimum_frames':8,'minimum_coverage':.7,'maximum_gap_ms':250,
        'minimum_duration_ms':500,'features':'ordered_angles_and_adjacent_differences',
        'side_policy':'most_complete_frames_tie_left',
        'classifier':{'kernel':'rbf','C':1.,'gamma':'scale','class_weight':'balanced','seed':SEED}}
REPORT=ROOT/'data/experiments/deadlift-temporal.json'


def trajectory_features(stamps,rows):
    times=np.asarray(stamps,dtype=float);values=np.asarray(rows,dtype=float)
    if times.ndim!=1 or values.shape!=(len(times),3):raise ValueError('时序特征形状不符')
    if not np.isfinite(times).all() or (len(times)>1 and not np.all(np.diff(times)>0)):
        raise ValueError('源时间必须有限且严格递增')
    valid=np.isfinite(values).all(axis=1);count=int(valid.sum())
    summary={'frames':len(times),'valid_frames':count,'coverage':count/len(times) if len(times) else 0.}
    if count<CONFIG['minimum_frames'] or summary['coverage']<CONFIG['minimum_coverage']:
        return None,'INSUFFICIENT_ANGLE_COVERAGE',summary
    good=times[valid]
    if times[-1]-times[0]<CONFIG['minimum_duration_ms']:return None,'SHORT_SOURCE_SPAN',summary
    gap=max(good[0]-times[0],times[-1]-good[-1],float(np.diff(good).max()))
    if gap>CONFIG['maximum_gap_ms']:return None,'ANGLE_EVIDENCE_GAP',summary
    target=np.linspace(good[0],good[-1],CONFIG['bins'])
    sampled=np.stack([np.interp(target,good,values[valid,i]) for i in range(3)],axis=1)
    result=np.concatenate((sampled.ravel(),np.diff(sampled,axis=0).ravel()))
    return result.tolist(),None,{**summary,'maximum_observed_gap_ms':gap,'dimension':len(result)}


def require_development(samples):
    if any(s.action_id!='conventional_deadlift' or s.partition!='development' or s.label not in ('GB','PB') for s in samples):
        raise ValueError('仅允许原硬拉development GB/PB，禁止独立test或无标签素材')


def fit_fold(training,validation,features):
    require_development(training+validation)
    if {s.key for s in training}&{s.key for s in validation} or {s.subject_group for s in training}&{s.subject_group for s in validation}:
        raise ValueError('拟合与验证人物／来源交叉')
    usable=[s for s in training if features[s.key]['features'] is not None]
    labels=[int(s.label=='PB') for s in usable]
    if set(labels)!={0,1}:raise ValueError('可用拟合样本缺少一类')
    model=make_pipeline(StandardScaler(),SVC(kernel='rbf',C=1.,gamma='scale',class_weight='balanced',random_state=SEED))
    model.fit(np.asarray([features[s.key]['features'] for s in usable]),labels)
    return model,{'requested_fit_keys':[s.key for s in training],'actual_fit_keys':[s.key for s in usable],
                  'fit_subjects':sorted({s.subject_group for s in usable}),'evaluation_keys':[s.key for s in validation],
                  'scaler_mean':model.named_steps['standardscaler'].mean_.tolist()}


def extract(sample,path):
    require_development([sample])
    with np.load(path,allow_pickle=False) as cache:
        meta=json.loads(str(cache['metadata']));stat=sample.absolute_path.stat()
        if meta['source']!={'source_sha256':sample.sha256,'bytes':stat.st_size,'modified_ns':stat.st_mtime_ns}:
            raise ValueError('原片与姿态缓存身份不一致')
        sides={'left':[],'right':[]};stamps=cache['timestamps_ms']
        for i,stamp in enumerate(stamps):
            raw=cache['points'][i]
            poses=[[dict(zip(('x','y','z','visibility','presence'),map(float,p))) for p in raw]] if int(cache['person_counts'][i])==1 else []
            row=frame_angles('conventional_deadlift',{'poses':poses,'width':int(cache['sizes'][i,0]),
                'height':int(cache['sizes'][i,1]),'identity_accepted':bool(cache['identity_accepted'][i])})
            for side in sides:sides[side].append(row[side] if row[side] is not None else [float('nan')]*3)
        side=max(sides,key=lambda s:sum(np.isfinite(r).all() for r in sides[s]))
        vector,reason,summary=trajectory_features(stamps,sides[side])
        return {'features':vector,'reason':reason,'summary':{**summary,'side':side}}


def development(reuse_only=False):
    samples=[s for s in load_dataset() if s.action_id=='conventional_deadlift' and s.partition=='development']
    require_development(samples)
    if len(samples)!=97:raise ValueError('固定开发分区数量改变')
    baseline=json.loads((ROOT/'data/experiments/baseline.json').read_text(encoding='utf-8'))
    paths={};identities={}
    for sample in samples:
        entry=baseline['extractions'][sample.key]
        feature=json.loads((ROOT/'data/experiments/features'/f"{entry['feature_key']}.json").read_text(encoding='utf-8'))
        path=project_path(feature['pose_cache']);path.relative_to(ROOT/'data/experiments/poses')
        paths[sample.key]=path
        # 既有源文件状态校验留在extract，缓存身份包含实际姿态文件摘要。
        stat=sample.absolute_path.stat()
        identities[sample.key]={'source_sha256':sample.sha256,'source_bytes':stat.st_size,'source_modified_ns':stat.st_mtime_ns,
            'pose_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'label':sample.label,
            'subject_group':sample.subject_group,'validation_fold':sample.validation_fold}
    code={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in
          ('fitness_app/deadlift_temporal.py','fitness_app/research_features.py','fitness_app/research_metrics.py',
           'fitness_app/frame_pipeline.py','fitness_app/upstream.py')}
    signature=hashlib.sha256(json.dumps({'config':CONFIG,'inputs':identities,'implementation':code,
        'split':hashlib.sha256((ROOT/'data/experiments/split.json').read_bytes()).hexdigest()},sort_keys=True).encode()).hexdigest()
    if REPORT.exists():
        previous=json.loads(REPORT.read_text(encoding='utf-8'))
        if previous.get('signature')==signature and previous.get('status')=='computed':return previous,True
    if reuse_only:raise ValueError('无匹配已算结果，不启动新拟合')
    features={s.key:extract(s,paths[s.key]) for s in samples}
    predictions=[];folds=[]
    for fold in range(4):
        train=[s for s in samples if s.validation_fold!=fold];validation=[s for s in samples if s.validation_fold==fold]
        model,identity=fit_fold(train,validation,features)
        folds.append({'fold':fold,**identity})
        for sample in validation:
            item=features[sample.key]
            prediction=int(model.predict([item['features']])[0]) if item['features'] is not None else None
            predictions.append({'sample_key':sample.key,'truth':int(sample.label=='PB'),'prediction':prediction,
                'reason':item['reason'] if prediction is None else None,'subject_group':sample.subject_group,'view':'|'.join(sample.views)})
    result={'status':'computed','signature':signature,'config':CONFIG,'implementation_sha256':code,'inputs':identities,
        'folds':folds,'predictions':predictions,'metrics':evaluate(predictions),
        'feature_summaries':{key:value['summary'] for key,value in features.items()},
        'new_pose_inference_frames':0,'new_training_fits':4,'test_used':False,'deployment':'not_deployed',
        'truth_scope':'source_clip_GB_PB_only','limitation':'来源整段类别，不是具体错误部位或错误时段真值；投影轨迹不是脊柱测量'}
    atomic_json(REPORT,result)
    return result,False
