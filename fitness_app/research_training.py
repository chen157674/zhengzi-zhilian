"""固定开发划分上的候选比较；所有拟合清单可追溯，拒判不移出评价分母。"""
import importlib.metadata
import hashlib
import json
import statistics
import time

import numpy as np
from sklearn.dummy import DummyClassifier
from sklearn.ensemble import RandomForestClassifier
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVC

from .research_data import SEED,SUPPORTED
from .research_extract import OUTPUT,atomic_json
from .research_metrics import evaluate

METHODS=(
    {'id':'majority','family':'majority'},
    *({'id':f'svm-c{value}','family':'svm','C':value} for value in (.1,1,10)),
    {'id':'rf-depth4','family':'rf','max_depth':4},
    {'id':'rf-depth-all','family':'rf','max_depth':None},
)


def truth(sample):return 1 if sample.label in {'bad','PB'} else 0


def fit_method(method,samples,features):
    if any(sample.partition in {'test','excluded'} for sample in samples):raise ValueError('禁止在测试／隔离样本上拟合')
    usable=[sample for sample in samples if features[sample.key].get('features') is not None]
    if not usable:raise ValueError('没有满足固定输入门槛的训练样本')
    x=np.asarray([features[sample.key]['features'] for sample in usable],dtype=float);y=np.asarray([truth(sample) for sample in usable])
    if not np.isfinite(x).all():raise ValueError('训练向量不是有限数')
    if method['family']!='majority' and len(set(y))<2:raise ValueError('训练输入不足两个来源类别')
    if method['family']=='majority':model=DummyClassifier(strategy='most_frequent')
    elif method['family']=='svm':model=make_pipeline(StandardScaler(),SVC(kernel='linear',C=method['C'],class_weight=None,random_state=SEED))
    elif method['family']=='rf':model=RandomForestClassifier(n_estimators=100,max_depth=method['max_depth'],random_state=SEED,class_weight=None,n_jobs=1)
    else:raise ValueError('未登记的方法')
    began=time.perf_counter();model.fit(x,y)
    record={'requested_fit_keys':[sample.key for sample in samples],'actual_fit_keys':[sample.key for sample in usable],
        'class_counts':{str(label):int((y==label).sum()) for label in (0,1)},'fit_ms':(time.perf_counter()-began)*1000,
        'feature_gate':'common_pose_feature_gate_for_all_methods','scaler_mean':model.named_steps['standardscaler'].mean_.tolist() if method['family']=='svm' else None}
    return model,record


def predict_method(model,samples,features,fit_error=None):
    rows=[]
    for sample in samples:
        source=features[sample.key];vector=source.get('features');prediction=None;elapsed=None
        reason=fit_error or source.get('error_code') or source.get('rejection_reason')
        if model is not None and vector is not None:
            began=time.perf_counter();prediction=int(model.predict(np.asarray([vector],dtype=float))[0]);elapsed=(time.perf_counter()-began)*1000;reason=None
        rows.append({'sample_key':sample.key,'truth':truth(sample),'prediction':prediction,'reason':reason,
            'subject_group':sample.subject_group,'view':'|'.join(sample.views),'prediction_ms':elapsed})
    return rows


def choose(results,require_threshold=True):
    computed=[row for row in results if row.get('status')=='computed' and row.get('metrics',{}).get('main') and row['metrics']['main']['macro_f1'] is not None]
    if not computed:return None
    def threshold(row):
        metrics=row['metrics']['main']
        return metrics['coverage']>=.8 and metrics['positive_recall'] is not None and metrics['positive_recall']>=.8 and all(item['coverage'] is not None and item['coverage']>=.7 for item in metrics['classes'])
    qualified=[row for row in computed if threshold(row)]
    if require_threshold and not qualified:return None
    pool=qualified or computed;best=max(row['metrics']['main']['macro_f1'] for row in pool)
    ties=[row for row in pool if best-row['metrics']['main']['macro_f1']<=.01]
    selected=min(ties,key=lambda row:(row['median_prediction_ms'] if row['median_prediction_ms'] is not None else float('inf'),{'majority':0,'svm':1,'rf':2}[row['method']['family']]))
    return {'method_id':selected['method']['id'],'development_coverage_recall_met':threshold(selected),
        'reason':'覆盖率／正类召回优先，主宏 F1；差值不超过 0.01 时比较预测耗时与方法复杂度'}


def compare_action(action,samples,features):
    samples=[sample for sample in samples if sample.action_id==action and sample.partition not in {'test','excluded'}]
    folds=[([sample for sample in samples if sample.validation_fold!=fold],[sample for sample in samples if sample.validation_fold==fold]) for fold in range(4)] if action=='conventional_deadlift' else [([sample for sample in samples if sample.partition=='train'],[sample for sample in samples if sample.partition=='validation'])]
    results=[]
    for method in METHODS:
        records=[];predictions=[]
        for index,(training,validation) in enumerate(folds):
            if {sample.key for sample in training}&{sample.key for sample in validation}:raise ValueError('拟合与验证样本交叉')
            if {sample.subject_group for sample in training}&{sample.subject_group for sample in validation}:raise ValueError('拟合与验证人物交叉')
            try:model,record=fit_method(method,training,features);error=None
            except ValueError as caught:model=None;error=str(caught);record={'requested_fit_keys':[sample.key for sample in training],'actual_fit_keys':[],'error':error}
            rows=predict_method(model,validation,features,error)
            record.update(fold=index,evaluation_keys=[sample.key for sample in validation]);records.append(record);predictions.extend(rows)
        timings=[row['prediction_ms'] for row in predictions if row['prediction_ms'] is not None]
        results.append({'method':method,'status':'computed' if any(record['actual_fit_keys'] for record in records) else 'failed',
            'folds':records,'predictions':predictions,'metrics':evaluate(predictions),'median_prediction_ms':statistics.median(timings) if timings else None})
    selected=choose(results)
    families={family:choose([row for row in results if row['method']['family']==family],require_threshold=False) for family in ('majority','svm','rf')}
    return {'action_id':action,'truth_scope':'source_clip_labels_only','primary':selected,'primary_status':'selected' if selected else 'development_targets_unmet',
        'reference_candidate':choose(results,require_threshold=False),'selected_by_family':families,'methods':results,
        'geometry_rule':{'status':'not_applicable','reason':'当前来源整体 good/bad 或 GB/PB 无经核实的几何阈值定义，不编造专家角度','metrics':None}}


def development_comparison(samples,baseline):
    expected={sample.key for sample in samples if sample.partition not in {'test','excluded'}}
    if expected-set(baseline['extractions']):raise ValueError('请完成开发特征处理后进行比较')
    features={};feature_hashes={};by_key={sample.key:sample for sample in samples}
    for key in sorted(expected):
        feature_key=baseline['extractions'][key]['feature_key'];path=OUTPUT/'features'/f'{feature_key}.json';raw=path.read_bytes();result=json.loads(raw)
        sample=by_key[key];stat=sample.absolute_path.stat()
        expected_source={'source_sha256':sample.sha256,'bytes':stat.st_size,'modified_ns':stat.st_mtime_ns}
        if result['sample_key']!=key or result['feature_key']!=feature_key or result['pose_key']!=baseline['extractions'][key]['pose_key'] or result['config_sha256']!=baseline['feature_config_sha256'] or result['source']!=expected_source:raise ValueError('特征身份或输入版本不一致')
        features[key]=result
        feature_hashes[key]=hashlib.sha256(raw).hexdigest()
    results={'schema_version':1,'phase':'development','split_sha256':baseline['split_sha256'],'feature_config_sha256':baseline['feature_config_sha256'],
        'pose_config':baseline['pose_config'],'feature_input_sha256':feature_hashes,
        'versions':{name:importlib.metadata.version(name) for name in ('numpy','scikit-learn','mediapipe','av')},
        'test_used':False,'actions':[compare_action(action,samples,features) for action in sorted(SUPPORTED)]}
    atomic_json(OUTPUT/'development-results.json',results)
    return results
