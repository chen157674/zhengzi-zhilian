"""开发选择后的冻结与独立测试；源真值仅用于评分，不进入预测特征。"""
import hashlib
import importlib.metadata
import json
import time
from io import BytesIO

import joblib

from .paths import ROOT,project_path
from .research_data import SUPPORTED,load_dataset,split_snapshot
from .research_extract import OUTPUT,atomic_json,extract,pose_config
from .research_features import CONFIG_SHA256
from .research_metrics import evaluate
from .research_training import METHODS,fit_method,predict_method


def _read(path):return json.loads(path.read_text(encoding='utf-8'))
def _hash(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def _feature_map(samples,baseline,expected_hashes):
    result={}
    for sample in samples:
        key=baseline['extractions'][sample.key]['feature_key'];path=OUTPUT/'features'/f'{key}.json'
        if _hash(path)!=expected_hashes.get(sample.key):raise ValueError('请根据当前特征重新冻结选择')
        data=_read(path)
        if data['sample_key']!=sample.key or data['feature_key']!=key or data['config_sha256']!=CONFIG_SHA256:raise ValueError('特征缓存与冻结配置不匹配')
        result[sample.key]=data
    return result


def freeze():
    if (OUTPUT/'freeze.json').exists():raise ValueError('冻结快照已存在，不重新拟合或覆盖')
    samples=load_dataset();baseline=_read(OUTPUT/'baseline.json');development_path=OUTPUT/'development-results.json';development=_read(development_path)
    if baseline.get('test_used') or baseline.get('frozen'):raise ValueError('该基线已冻结或使用过独立测试')
    split=split_snapshot(samples)
    if baseline['split_sha256']!=split['split_sha256'] or development['split_sha256']!=split['split_sha256']:raise ValueError('开发／冻结划分不同')
    if baseline['pose_config']!=pose_config() or development['pose_config']!=baseline['pose_config'] or baseline['feature_config_sha256']!=CONFIG_SHA256 or development['feature_config_sha256']!=CONFIG_SHA256:raise ValueError('请根据当前配置重新冻结结果')
    registered={method['id']:method for method in METHODS};artifacts=[]
    for action in sorted(SUPPORTED):
        chosen=next(row for row in development['actions'] if row['action_id']==action)
        train=[sample for sample in samples if sample.action_id==action and sample.partition not in {'test','excluded'}]
        features=_feature_map(train,baseline,development['feature_input_sha256']);models=[]
        for family in ('majority','svm','rf'):
            selection=chosen['selected_by_family'][family]
            if selection is None:models.append({'family':family,'status':'unavailable','reason':'开发阶段没有可用拟合结果'});continue
            method=registered[selection['method_id']]
            try:model,fit_record=fit_method(method,train,features)
            except ValueError as error:models.append({'family':family,'method':method,'status':'unavailable','reason':str(error)});continue
            path=project_path(OUTPUT/'models'/f"{action}__{method['id']}.joblib");path.parent.mkdir(parents=True,exist_ok=True)
            temporary=path.with_suffix('.joblib.part');owned=False
            try:
                with temporary.open('xb') as stream:owned=True;joblib.dump(model,stream,compress=3)
                temporary.replace(path)
            finally:
                if owned and temporary.exists():temporary.unlink()
            models.append({'family':family,'method':method,'status':'ready','path':path.relative_to(ROOT).as_posix(),'sha256':_hash(path),'fit':fit_record})
        artifacts.append({'action_id':action,'primary':chosen['primary'],'primary_status':chosen['primary_status'],'reference_candidate':chosen['reference_candidate'],'methods':models,'geometry_rule':chosen['geometry_rule']})
    frozen={'schema_version':1,'frozen':True,'frozen_at_ms':time.time_ns()//1_000_000,'split_sha256':split['split_sha256'],
        'pose_config':baseline['pose_config'],'feature_config_sha256':CONFIG_SHA256,'versions':development['versions'],
        'development_results_sha256':_hash(development_path),'test_keys':[sample.key for sample in samples if sample.partition=='test'],
        'actions':artifacts,'acceptance':{'positive_precision':.85,'positive_recall':.8,'macro_f1':.8,'coverage':.8,'class_coverage':.7}}
    atomic_json(OUTPUT/'freeze.json',frozen);baseline['frozen']=True;atomic_json(OUTPUT/'baseline.json',baseline)
    return frozen


def independent_test():
    frozen=_read(OUTPUT/'freeze.json');baseline=_read(OUTPUT/'baseline.json');samples=load_dataset();split=split_snapshot(samples)
    if not frozen.get('frozen') or not baseline.get('frozen'):raise ValueError('独立测试前必须完成冻结')
    if frozen['split_sha256']!=split['split_sha256'] or frozen['pose_config']!=pose_config() or frozen['feature_config_sha256']!=CONFIG_SHA256:raise ValueError('当前输入／实现与冻结快照不同，停止测试')
    if any(importlib.metadata.version(name)!=version for name,version in frozen['versions'].items()):raise ValueError('运行依赖与冻结版本不同')
    if _hash(OUTPUT/'development-results.json')!=frozen['development_results_sha256']:raise ValueError('开发结果在冻结后发生变化')
    frozen_hash=_hash(OUTPUT/'freeze.json');result_path=OUTPUT/'test-results.json'
    if result_path.exists():
        previous=_read(result_path)
        if previous['freeze_sha256']!=frozen_hash:raise ValueError('已有测试使用了不同冻结快照')
        return previous  # 供页面复用真实预测，不重跑或重新选赢家。
    selected=[sample for sample in samples if sample.partition=='test']
    if {sample.key for sample in selected}!=set(frozen['test_keys']):raise ValueError('测试成员与冻结清单不一致')
    models={}
    for action_config in frozen['actions']:
        for item in action_config['methods']:
            if item['status']!='ready':continue
            path=project_path(item['path']);path.relative_to(OUTPUT/'models');blob=path.read_bytes()
            if hashlib.sha256(blob).hexdigest()!=item['sha256']:raise ValueError('请核对冻结模型后读取测试输入')
            # 校验和加载使用同一份已核验字节；只有本机本次训练产物可进入此处。
            models[action_config['action_id'],item['method']['id']]=joblib.load(BytesIO(blob))
    # 在读取第一幅测试图像之前持久记录已经使用；失败后仍不能称测试未见。
    baseline['test_used']=True;baseline.setdefault('first_test_started_at_ms',time.time_ns()//1_000_000);atomic_json(OUTPUT/'baseline.json',baseline)
    features={}
    for index,sample in enumerate(selected,1):
        result=extract(sample,frozen['pose_config'],allow_test=True);features[sample.key]=result
        print(json.dumps({'phase':'independent_test','index':index,'total':len(selected),'action':sample.action_id,'status':result['status'],'rejection':result.get('rejection_reason'),'cache_hit':result['cache_hit']},ensure_ascii=False),flush=True)
    actions=[]
    for action_config in frozen['actions']:
        action=action_config['action_id'];action_samples=[sample for sample in selected if sample.action_id==action];methods=[]
        for item in action_config['methods']:
            if item['status']!='ready':methods.append({**item,'metrics':None,'predictions':[]});continue
            model=models[action,item['method']['id']];rows=predict_method(model,action_samples,features)
            methods.append({'method':item['method'],'status':'computed','predictions':rows,'metrics':evaluate(rows)})
        actions.append({'action_id':action,'primary':action_config['primary'],'primary_status':action_config['primary_status'],'reference_candidate':action_config['reference_candidate'],'methods':methods,'geometry_rule':action_config['geometry_rule']})
    result={'schema_version':1,'phase':'independent_test','freeze_sha256':frozen_hash,'test_used':True,'completed_at_ms':time.time_ns()//1_000_000,
        'split_sha256':split['split_sha256'],'truth_scope':'source_clip_labels_only','actions':actions,
        'unsupported_metrics':{'keypoint_precision':'无独立关键点真值','cycle_count_accuracy':'无独立逐次周期真值','specific_issue_accuracy':'整体来源标签不支持具体判项','localization_accuracy':'无独立逐帧／周期问题范围真值'}}
    atomic_json(result_path,result)
    return result
