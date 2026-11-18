"""管理员实验的固定资源目录。只读已冻结预测，不在页面请求中训练模型。"""
import copy
import csv
from functools import lru_cache
import hashlib
import io
import json

from .paths import ROOT,project_path
from .database import json_text
from .auth import APIError
from .research_data import INDEX_SHA256,MANIFEST_SHA256

TASKS={
    'EXP-02':('conventional_deadlift','硬拉 · GB/PB 片段分类','GB','PB'),
    'EXP-03A':('bodyweight_squat','深蹲 · good/bad 片段分类','good','bad'),
    'EXP-03B':('standard_push_up','俯卧撑 · good/bad 片段分类','good','bad'),
    'EXP-03C':('standing_dumbbell_curl','弯举 · good/bad 片段分类','good','bad'),
}
ROOT_DATA=ROOT/'data/experiments'


def _digest(blob):return hashlib.sha256(blob).hexdigest()


@lru_cache(maxsize=32)
def _model_hash(path,modified,size):return _digest(project_path(path).read_bytes())


def model_state(item):
    if item.get('status')!='ready':return False,item.get('reason','请选择已准备的分析方法')
    try:
        path=project_path(item['path']);path.relative_to(ROOT_DATA/'models');stat=path.stat()
        if _model_hash(str(path),stat.st_mtime_ns,stat.st_size)!=item['sha256']:return False,'模型文件与冻结摘要不一致'
    except (OSError,ValueError,KeyError):return False,'模型资源缺失或路径无效'
    return True,None


class ExperimentSources:
    def __init__(self):self._signature=None;self._cached=None

    def _core(self):
        paths=[ROOT_DATA/name for name in ('baseline.json','freeze.json','test-results.json','split.json')]+[ROOT/'data/datasets'/name for name in ('index.csv','manifest.json')]
        signature=tuple((path.stat().st_mtime_ns,path.stat().st_size) for path in paths)
        if signature==self._signature:return self._cached
        blobs=[path.read_bytes() for path in paths];baseline,frozen,results,split=map(json.loads,blobs[:4]);publication=baseline.get('published_comparison',{})
        if not baseline.get('frozen') or not baseline.get('test_used') or publication.get('schema_version')!=1:raise ValueError('请完成实验冻结与测试后查看结果')
        if _digest(blobs[1])!=publication.get('freeze_sha256') or _digest(blobs[2])!=publication.get('results_sha256'):raise ValueError('结果文件与已核发布摘要不符')
        if results.get('freeze_sha256')!=publication['freeze_sha256'] or not frozen.get('frozen') or not results.get('test_used'):raise ValueError('实验版本引用不一致')
        split_hash=_digest(json_text({'assignments':split['assignments']}).encode())
        if any(value!=split_hash for value in (split['split_sha256'],frozen['split_sha256'],results['split_sha256'])):raise ValueError('固定划分摘要不一致')
        if _digest(blobs[4])!=INDEX_SHA256 or _digest(blobs[5])!=MANIFEST_SHA256:raise ValueError('数据清单与已确认版本不符')
        rows=list(csv.DictReader(io.StringIO(blobs[4].decode('utf-8-sig'))));index={row['dataset_id']+':'+row['sha256']:row for row in rows}
        selected={row['sample_key'] for row in split['assignments'] if row['partition']=='test'}
        if selected!=set(frozen['test_keys']):raise ValueError('固定测试成员不一致')
        for action in results['actions']:
            expected={key for key in selected if index[key]['action_id']==action['action_id']}
            for method in action['methods']:
                if method.get('status')!='computed':continue
                predictions=method['predictions'];keys=[row['sample_key'] for row in predictions]
                if len(keys)!=len(set(keys)) or set(keys)!=expected:raise ValueError('原始预测样本分母不一致')
                if any(row['truth']!=int(index[row['sample_key']]['source_labels'] in {'PB','bad'}) for row in predictions):raise ValueError('原始预测真值与来源标签不同')
        data={'publication':publication,'frozen':frozen,'results':results,'index':index,'split_hash':split_hash,'test_keys':selected}
        self._signature=signature;self._cached=data;return data

    def _task(self,task_id,core):
        action_id,title,negative,positive=TASKS[task_id]
        frozen=next(row for row in core['frozen']['actions'] if row['action_id']==action_id)
        result=next(row for row in core['results']['actions'] if row['action_id']==action_id)
        keys=[key for key in core['test_keys'] if core['index'][key]['action_id']==action_id];missing=0
        available_samples=sum(row['action_id']==action_id and row['annotation_status']=='consistent_source_label' for row in core['index'].values())
        for key in keys:
            row=core['index'][key]
            try:
                path=project_path(ROOT/'data/datasets'/row['path']);path.relative_to(ROOT/'data/datasets')
                if not path.is_file() or path.stat().st_size!=int(row['bytes']):missing+=1
            except (OSError,ValueError):missing+=1
        methods=[]
        for model in frozen['methods']:
            method=model.get('method');family=model['family']
            if method is None:
                methods.append({'method_id':family,'name':family,'ready':False,'reason':model.get('reason','资源未就绪')});continue
            ready,reason=model_state(model)
            cached=next((row for row in result['methods'] if row.get('method',{}).get('id')==method['id'] and row.get('status')=='computed'),None)
            if cached is None:ready=False;reason='没有该冻结方法的真实预测'
            if missing:ready=False;reason=f'数据文件缺失或大小变化（{missing} 段）'
            name='多数类基线' if family=='majority' else f"线性 SVM（C={method['C']}）" if family=='svm' else f"随机森林（深度 {method['max_depth'] if method['max_depth'] is not None else '不限'}）"
            methods.append({'method_id':method['id'],'name':name,'family':family,'ready':ready,'reason':reason,'cached_result':cached is not None})
        methods.append({'method_id':'geometry-rule','name':'几何阈值基线','ready':False,'reason':'请选择与现有标注对应的评估方法。','status':'not_applicable'})
        return {'task_id':task_id,'action_id':action_id,'name':title,'dataset_version':core['publication']['dataset_version'],'dataset_name':'固定公开视频 · 来源片段标签',
            'split_hash':core['split_hash'],'samples':len(keys),'available_samples':available_samples,'development_samples':available_samples-len(keys),
            'subject_group_count':len({core['index'][key]['subject_group'] for key in keys}),
            'negative_label':negative,'positive_label':positive,'truth_scope':'source_clip_labels_only',
            'primary_status':frozen['primary_status'],'ready':sum(method['ready'] for method in methods)>=2,'methods':methods,
            'unavailable_metrics':core['results']['unsupported_metrics']}

    def options(self):
        try:
            core=self._core();return {'status':'ready','tasks':[self._task(task_id,core) for task_id in TASKS],
                'evaluation_mode':'verified_prediction_cache','note':'复用步骤 11 的冻结原始预测并汇总所选方法；不重新训练或推理视频。当前粗分类均未达预设目标。'}
        except (OSError,ValueError,KeyError,StopIteration,TypeError) as error:
            return {'status':'unavailable','tasks':[],'error_code':'EXPERIMENT_RESOURCES_UNAVAILABLE','message':'已有实验资源缺失或与冻结版本不一致，请先核对本机资源。','diagnostic_type':type(error).__name__}

    def snapshot(self,payload):
        if set(payload)!={'task_id','dataset_version','split_hash','method_ids'}:raise APIError('INVALID_REQUEST','实验只接受已登记的任务、数据和方法',400)
        task_id=payload['task_id'];methods=payload['method_ids']
        if not isinstance(task_id,str) or task_id not in TASKS:raise APIError('VALIDATION_FAILED','请选择已有实验任务',422)
        if not isinstance(methods,list) or not 2<=len(methods)<=3 or any(not isinstance(item,str) for item in methods) or len(set(methods))!=len(methods):raise APIError('VALIDATION_FAILED','请选择至少两种不同的就绪方法',422,{'method_ids':'需要两至三种不同方法'})
        try:core=self._core();task=self._task(task_id,core)
        except (OSError,ValueError,KeyError,StopIteration,TypeError) as error:raise APIError('EXPERIMENT_RESOURCES_UNAVAILABLE','实验资源未就绪',503) from error
        if payload['dataset_version']!=task['dataset_version'] or payload['split_hash']!=task['split_hash']:raise APIError('STALE_EXPERIMENT_VERSION','数据或划分版本已变化，请重新读取选项',409)
        allowed={item['method_id']:item for item in task['methods']}
        if any(method not in allowed or not allowed[method]['ready'] for method in methods):raise APIError('METHOD_UNAVAILABLE','所选方法未就绪，不自动替换其他方法',422,{'method_ids':'请核对资源状态'})
        action=next(row for row in core['results']['actions'] if row['action_id']==task['action_id'])
        selected={method:copy.deepcopy(next(row for row in action['methods'] if row.get('method',{}).get('id')==method)) for method in methods}
        return {'config':{**copy.deepcopy(payload),'task_name':task['name'],'action_id':task['action_id'],'samples':task['samples'],'subject_group_count':task['subject_group_count'],
            'available_samples':task['available_samples'],'development_samples':task['development_samples'],
            'methods':[{'method_id':method,'name':allowed[method]['name'],'parameters':copy.deepcopy(selected[method]['method'])} for method in methods],
            'positive_label':task['positive_label'],'negative_label':task['negative_label'],'truth_scope':task['truth_scope'],
            'primary_status':task['primary_status'],'evaluation_mode':'verified_prediction_cache','freeze_sha256':core['publication']['freeze_sha256'],
            'results_sha256':core['publication']['results_sha256'],'versions':copy.deepcopy(core['frozen']['versions']),
            'prediction_timing':copy.deepcopy(core['publication'].get('prediction_timing')),
            'frozen_at_ms':core['frozen']['frozen_at_ms'],'source_evaluated_at_ms':core['results']['completed_at_ms'],
            'unavailable_metrics':copy.deepcopy(task['unavailable_metrics'])},'methods':selected}
