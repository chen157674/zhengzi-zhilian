"""发布已验证的运动确认保护，只改元数据，不重训/覆盖基础33类权重。"""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment
configure_environment()
import argparse,json,hashlib
from fitness_app.motion_evidence import VERSION,policy_signature,read_policy
from fitness_app.research_extract import atomic_json
from fitness_app.semantic_model import resources


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--replace-policy',action='store_true');args=parser.parse_args()
    path=ROOT/'data/models/semantic-action-classifier.json';evaluation=ROOT/'data/verification/round3/motion-evidence-evaluation.json'
    raw=path.read_bytes();info=json.loads(raw);report=json.loads(evaluation.read_bytes())
    if not report['safe_nonregression_candidate'] or report['unreviewed_correct_vetoes']:raise RuntimeError('尚有未解释的真实动作退步，不发布')
    if info.get('motion_evidence') and not args.replace_policy:
        read_policy(info);print('Current motion policy already enabled:',info['version']);return
    base_version=info.get('base_version',info['version'])
    if base_version!=report['base_model_version']:raise ValueError('基础模型身份改变')
    config=report['selected']['config'];signature=policy_signature(config)
    if signature!=report['policy_signature']:raise ValueError('评估后运动代码或参数改变')
    previous_version=info['version'];new={**info,'base_version':base_version,'version':base_version+'-motion-'+signature[:10],
        'motion_evidence':{'version':VERSION,'config':config,'signature':signature,'base_weight_sha256':info['sha256'],
            'evaluation_path':evaluation.relative_to(ROOT).as_posix(),'evaluation_sha256':hashlib.sha256(evaluation.read_bytes()).hexdigest(),
            'scope':'activity_confirmation_only_not_technique_or_independent_accuracy'}}
    read_policy(new)
    atomic_json(path,new)
    try:
        loaded,reason=resources()
        if loaded is None:raise RuntimeError('新策略资源验证失败：'+str(reason))
    except BaseException:
        # 仅恢复本次原子修改的同一元数据；不额外复制权重或广泛备份。
        path.write_bytes(raw);raise
    atomic_json(ROOT/'data/verification/round3/motion-evidence-release.json',{
        'base_model_version':base_version,'previous_model_version':previous_version,'model_version':new['version'],'base_weight_sha256':info['sha256'],'weights_changed':False,
        'previous_manifest_sha256':hashlib.sha256(raw).hexdigest(),'manifest_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
        'policy_signature':signature,'evaluation_sha256':new['motion_evidence']['evaluation_sha256'],'runtime_enabled':True,
        'raw_original_protocol':{'correct_before':report['correct_before'],'correct_after':report['correct_after'],'positive_clips':report['validation_positive_clips']},
        'reviewed_development_protocol':report['phase_reviewed_validation'],'independent_accuracy_passed':False,'requires_service_restart':True})
    print('Enabled activity confirmation:',new['version'],'; base weights unchanged.')


if __name__=='__main__':main()
