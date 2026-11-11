"""只在开发、保留集和真实HTTP链均满足基础门槛后启用本地视频模型。"""
import hashlib,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment
configure_environment()
from fitness_app.semantic_model import resources,MANIFEST
from fitness_app.research_extract import atomic_json


def run():
    info,reason=resources(research=True)
    if info is None:raise ValueError(reason)
    directory=ROOT/'data/verification/step-22'
    names=('video-release-calibration.json','semantic-holdout.json','semantic-realtime-flow.json','semantic-coaching-handoff.json')
    evidence={name:json.loads((directory/name).read_text(encoding='utf-8')) for name in names}
    calibration,heldout,flow,coaching=[evidence[n] for n in names]
    if not info.get('basic_development_gate_passed') or not calibration['basic_development_gate_passed']:raise ValueError('基础开发指标未达标')
    if hashlib.sha256((directory/names[0]).read_bytes()).hexdigest()!=info['calibration_sha256']:raise ValueError('候选校准记录改变')
    if heldout['clips']<8 or heldout['metrics']['accuracy_including_rejections']<.7:raise ValueError('独立发布者保留集未达基础门槛')
    if not flow['passed'] or not flow['temporary_database_removed'] or not coaching['passed']:raise ValueError('HTTP自动分析/纠错交接未通过')
    if flow['steady_classifier_latency_ms']['p95']>5000:raise ValueError('实时分类延迟超过当前有效期')
    if flow['recognizer_code_sha256']!=hashlib.sha256((ROOT/'fitness_app/semantic_recognition.py').read_bytes()).hexdigest():raise ValueError('实时识别代码在回放后改变')
    for report in (flow,coaching):
        if report['head_sha256']!=info['sha256'] or report['model_version']!=info['version']:raise ValueError('验证的模型不是当前候选')
    if heldout['policy']['head_sha256']!=info['sha256'] or heldout['policy']['version']!=info['version']:raise ValueError('保留集模型版本不符')
    for path,digest in heldout['policy']['code'].items():
        if hashlib.sha256((ROOT/path).read_bytes()).hexdigest()!=digest:raise ValueError('保留集后推理实现改变: '+path)
    info.update(runtime_enabled=True,release_gate_passed=True,full_validation_passed=False,
                release_evidence={name:{'path':(directory/name).relative_to(ROOT).as_posix(),'sha256':hashlib.sha256((directory/name).read_bytes()).hexdigest()} for name in names},
                release_scope='basic automatic33-class video reference; not every-class70/full posture-error validation',
                cold_start_warm_ms=flow['cold_warm_ms'],steady_p95_ms=flow['steady_classifier_latency_ms']['p95'])
    atomic_json(MANIFEST,info)
    actual,reason=resources()
    if actual is None:raise ValueError('启用后资源校验失败: '+str(reason))
    print(json.dumps({'enabled':actual['runtime_enabled'],'version':actual['version'],'release_mode':actual['release_mode'],
                      'exercise_classes':actual['exercise_classes'],'full_validation_passed':actual['full_validation_passed']}),flush=True)


if __name__=='__main__':run()
