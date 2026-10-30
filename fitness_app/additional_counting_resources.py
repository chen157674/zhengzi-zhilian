"""14 的逐动作资源，独立冻结，避免新增一项改变原四动作版本。"""
from dataclasses import asdict
from functools import lru_cache
import hashlib
import inspect
import json

from .paths import ROOT,project_path
from .database import json_text
from .cycles import CycleConfig,CycleTracker
from .reverse_lunge import ReverseLungeConfig,ReverseLungeCounter,lunge_observation
from .plank_hold import PlankHoldConfig,PlankHoldCounter,plank_observation
from .jumping_jack import JumpingJackConfig,JumpingJackCounter,jack_observation
from .barbell_row import BarbellRowConfig,BarbellRowCounter,row_observation
from .frame_pipeline import usable_point,framing,REQUIRED,VISIBILITY_THRESHOLD,FRAME_SCHEMA_VERSION
from .upstream import calculate_angle
from .content_bounds import bounds_signature
from .counting_resources import read_input_validation

RESOURCES={'reverse_lunge':('reverse-lunge.json',ReverseLungeConfig,ReverseLungeCounter),
    'forearm_plank':('plank-hold.json',PlankHoldConfig,PlankHoldCounter),
    'jumping_jack':('jumping-jack.json',JumpingJackConfig,JumpingJackCounter),
    'barbell_bent_over_row':('barbell-row.json',BarbellRowConfig,BarbellRowCounter)}


def digest(value):return hashlib.sha256(json_text(value).encode()).hexdigest()


@lru_cache(maxsize=8)
def implementation_signature(action):
    if action not in RESOURCES:raise ValueError('动作尚未实现')
    model=json.loads((ROOT/'data/models/manifest.json').read_text(encoding='utf-8'))
    components={'reverse_lunge':(CycleConfig,CycleTracker,ReverseLungeConfig,ReverseLungeCounter,lunge_observation),
        'forearm_plank':(PlankHoldConfig,PlankHoldCounter,plank_observation),
        'jumping_jack':(CycleConfig,CycleTracker,JumpingJackConfig,JumpingJackCounter,jack_observation),
        'barbell_bent_over_row':(CycleConfig,CycleTracker,BarbellRowConfig,BarbellRowCounter,row_observation)}[action]
    return {'pose_model_sha256':model['sha256'],'frame_schema':FRAME_SCHEMA_VERSION,'visibility_threshold':VISIBILITY_THRESHOLD,'content_bounds':bounds_signature(),
        'framing_requirements':REQUIRED[action],
        'code':{item.__name__:hashlib.sha256(inspect.getsource(item).encode()).hexdigest() for item in
            (*components,usable_point,framing,calculate_angle)}}


def configuration(action,raw):
    config_type=RESOURCES[action][1]
    if action in {'reverse_lunge','jumping_jack','barbell_bent_over_row'}:return config_type(cycle=CycleConfig(**raw['cycle']),**{k:v for k,v in raw.items() if k!='cycle'})
    return config_type(**raw)


def read_manifest(action,path=None):
    if action not in RESOURCES:raise ValueError('动作尚未实现')
    filename,config_type,_=RESOURCES[action]
    path=project_path(path or ROOT/'data/models'/filename);doc=json.loads(path.read_text(encoding='utf-8'))
    if doc.get('schema_version')!=1 or doc.get('status')!='ready' or doc.get('action_id')!=action:raise ValueError('动作资源尚未冻结')
    raw=doc['config'];config=configuration(action,raw)
    if asdict(config)!=raw or digest(raw)!=doc['parameters_sha256']:raise ValueError('动作参数摘要不符')
    evidence=project_path(doc['calibration_path']);evidence.relative_to(ROOT/'data/verification/step-14')
    evidence_bytes=evidence.read_bytes()
    if hashlib.sha256(evidence_bytes).hexdigest()!=doc['calibration_sha256']:raise ValueError('开发诊断摘要不符')
    calibration=json.loads(evidence_bytes)
    if calibration['config']!=raw or calibration['action_id']!=action:raise ValueError('诊断对应其他动作参数')
    signature=implementation_signature(action)
    if signature!=doc['implementation']:raise ValueError('实现与冻结版本不匹配')
    read_input_validation(doc,action,signature)
    identity=digest({'parameters':doc['parameters_sha256'],'implementation':signature})
    return {**doc,'capability_version':'count-14-'+identity[:12]}


@lru_cache(maxsize=16)
def _cached(action,path,mtime,size,evidence_mtime,evidence_size,validation_mtime,validation_size):return read_manifest(action,path)


def current_manifest(action):
    if action not in RESOURCES:return None
    try:
        path=ROOT/'data/models'/RESOURCES[action][0];stat=path.stat()
        doc=json.loads(path.read_text(encoding='utf-8'));evidence=project_path(doc['calibration_path']);es=evidence.stat()
        validation=project_path(doc['input_validation_path']).stat()
        return _cached(action,str(path),stat.st_mtime_ns,stat.st_size,es.st_mtime_ns,es.st_size,validation.st_mtime_ns,validation.st_size)
    except (OSError,ValueError,KeyError,TypeError):return None


def create_additional_counter(action):
    manifest=current_manifest(action)
    if not manifest:return None
    _,config_type,counter_type=RESOURCES[action];raw=manifest['config']
    return counter_type(configuration(action,raw))
