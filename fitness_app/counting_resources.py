"""周期参数与实现身份校验；开发配置只供显式隔离验证使用。"""
from dataclasses import asdict
from functools import lru_cache
import hashlib
import inspect
import json
from pathlib import Path

from .paths import ROOT,project_path
from .database import json_text
from .action_counting import ACTIONS,ActionCounter,ActionCountConfig,side_observation
from .cycles import CycleConfig,CycleTracker
from .frame_pipeline import usable_point,framing,VISIBILITY_THRESHOLD,REQUIRED,FRAME_SCHEMA_VERSION
from .upstream import calculate_angle
from .content_bounds import bounds_signature

DEFAULT_CONFIG=ROOT/'data/models/cycle-counting.json'


def digest(value):return hashlib.sha256(json_text(value).encode()).hexdigest()


@lru_cache(maxsize=1)
def implementation_signature():
    model=json.loads((ROOT/'data/models/manifest.json').read_text(encoding='utf-8'))
    return {'visibility_threshold':VISIBILITY_THRESHOLD,'framing_requirements':REQUIRED,'frame_schema':FRAME_SCHEMA_VERSION,'pose_model_sha256':model['sha256'],'content_bounds':bounds_signature(),
        'code':{item.__name__:hashlib.sha256(inspect.getsource(item).encode()).hexdigest()
        for item in (CycleConfig,CycleTracker,ActionCountConfig,ActionCounter,side_observation,usable_point,framing,calculate_angle)}}


def used_dependencies(signature):
    """早期清单保存了全动作表；此资源只消费 ACTIONS 中四项的取景规则。"""
    return {**signature,'framing_requirements':{action:signature['framing_requirements'][action] for action in ACTIONS}}


def read_input_validation(document,key,expected_implementation):
    evidence=project_path(document['input_validation_path']);evidence.relative_to(ROOT/'data/verification/step-14')
    raw=evidence.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=document['input_validation_sha256']:raise ValueError('输入边界核查摘要不匹配')
    report=json.loads(raw)
    if report.get('schema_version')!=1 or report.get('content_bounds_signature')!=bounds_signature():raise ValueError('输入边界核查实现不匹配')
    if report['resource_parameters'].get(key)!=document['parameters_sha256'] or report['resource_implementations'].get(key)!=expected_implementation:raise ValueError('输入边界核查不属于当前动作参数或实现')
    actions=ACTIONS if key=='four_actions' else {key}
    for action in actions:
        result=report.get('actions',{}).get(action,{})
        if result.get('counter_cases',0)<=0 or max(result.get('events',0),result.get('hold_ms',0))<=0:raise ValueError('输入边界缺少实际动作完成量核查证据')
    return report


def read_counting_manifest(path=DEFAULT_CONFIG,allow_development=False):
    path=project_path(path)
    if not path.is_file():raise ValueError('周期参数资源缺失')
    document=json.loads(path.read_text(encoding='utf-8'))
    if document.get('schema_version')!=1 or document.get('status') not in ({'development','ready'} if allow_development else {'ready'}):raise ValueError('周期参数尚未冻结或版本不支持')
    if set(document.get('actions',{}))!=ACTIONS:raise ValueError('动作参数成员不匹配')
    if digest(document['actions'])!=document.get('parameters_sha256'):raise ValueError('动作参数摘要不匹配')
    for action,raw in document['actions'].items():
        config=ActionCountConfig(cycle=CycleConfig(**raw['cycle']),**{key:value for key,value in raw.items() if key!='cycle'})
        if config.algorithm_version!=document.get('algorithm_version') or asdict(config)!=raw:raise ValueError('动作参数契约不匹配')
    evidence=project_path(document['calibration_path']);evidence.relative_to(ROOT/'data/verification/step-12')
    raw_evidence=evidence.read_bytes()
    if hashlib.sha256(raw_evidence).hexdigest()!=document.get('calibration_sha256'):raise ValueError('开发诊断摘要不匹配')
    evidence_data=json.loads(raw_evidence)
    if evidence_data.get('parameters_sha256')!=document['parameters_sha256']:raise ValueError('开发证据对应其他参数')
    signature=implementation_signature()
    if document['status']=='ready':
        frozen=document.get('implementation',{})
        try:matches=used_dependencies(frozen)==used_dependencies(signature)
        except (KeyError,TypeError):matches=False
        if not matches:raise ValueError('当前计数实现与冻结版本不匹配')
        read_input_validation(document,'four_actions',used_dependencies(signature))
    # 仅在实际依赖全部相同后沿用原冻结身份；不因其他动作的规则变更改写历史四动作版本。
    identity=digest({'parameters':document['parameters_sha256'],'algorithm_version':document['algorithm_version'],
        'implementation':document['implementation'] if document['status']=='ready' else signature})
    return {**document,'capability_version':'count-1-'+identity[:12]}


@lru_cache(maxsize=8)
def _ready_manifest(path,modified,size,evidence_modified,evidence_size,validation_modified,validation_size):
    return read_counting_manifest(path)


def current_counting_manifest():
    try:
        stat=DEFAULT_CONFIG.stat()
        # 诊断文件也参与缓存身份，变更后不能继续沿用旧校验。
        evidence=ROOT/'data/verification/step-12/development-cycles.json';es=evidence.stat()
        doc=json.loads(DEFAULT_CONFIG.read_text(encoding='utf-8'));validation=project_path(doc['input_validation_path']).stat()
        return _ready_manifest(str(DEFAULT_CONFIG),stat.st_mtime_ns,stat.st_size,es.st_mtime_ns,es.st_size,validation.st_mtime_ns,validation.st_size)
    except (OSError,ValueError,KeyError,TypeError):return None


def create_counter(action_id,allow_development=False):
    if action_id not in ACTIONS:
        from .additional_counting_resources import create_additional_counter
        return create_additional_counter(action_id)
    manifest=read_counting_manifest(allow_development=True) if allow_development else current_counting_manifest()
    if not manifest or action_id not in manifest['actions']:return None
    raw=manifest['actions'][action_id]
    return ActionCounter(action_id,ActionCountConfig(cycle=CycleConfig(**raw['cycle']),**{key:value for key,value in raw.items() if key!='cycle'}))
