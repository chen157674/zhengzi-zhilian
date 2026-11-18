"""固定提交及摘要验证后的第三方资源；不联网、不自动下载。"""
import json
from functools import lru_cache
from .paths import ROOT,project_path
from .pose import checked_model_hash

MANIFEST=ROOT/'data/experiments/open-source-correction/manifest.json'


@lru_cache(maxsize=2)
def _registry(stamp,size):
    document=json.loads(MANIFEST.read_text(encoding='utf-8'))
    if document.get('owner')!='fitness-pretrained-correction':raise ValueError('预训练资源清单归属不符')
    return document


def registry():
    stat=MANIFEST.stat();return _registry(stat.st_mtime_ns,stat.st_size)


def checked(path,record):
    path=project_path(path);stat=path.stat()
    if stat.st_size!=record['bytes'] or not checked_model_hash(str(path),stat.st_mtime_ns,stat.st_size,record['sha256']):
        raise ValueError('预训练资源摘要不符：'+path.name)
    return path


def repo_asset(name,relative):
    record=registry()['repos'][name]
    if record['state']!='ready':raise ValueError('请准备源码资源')
    item=next((item for item in record['assets'] if item['path']==relative),None)
    if item is None:raise ValueError('资源未在固定源码清单中登记')
    return checked(project_path(record['path'])/relative,item),{**item,'source_commit':record['commit'],'license':record['license']}


def onnx_asset(name):
    record=registry()['models'][name]
    if record['state']!='ready':raise ValueError('请准备ONNX模型资源')
    item=next(item for item in record['files'] if item['path']==record['onnx'])
    return checked(item['path'],item),{**record,**item}
