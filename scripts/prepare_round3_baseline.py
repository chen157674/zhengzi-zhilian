"""从实际发布/目录/规则缓存建立第三轮比较基线；不执行模型推理。"""
import hashlib,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment
configure_environment()
from fitness_app.catalogue import definitions
from fitness_app.posture_rules import rules_for,policy_version
from fitness_app.research_extract import atomic_json

OUT=ROOT/'data/verification/round3/baseline.json'
INPUTS=('data/models/semantic-action-classifier.json','data/verification/step-22/video-release-calibration.json',
        'data/datasets/development-movements/gym-segments.json','fitness_app/catalogue.py',
        'fitness_app/posture_rules.py','fitness_app/gym_catalogue.py')


def run():
    identity={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in INPUTS}
    identity['script']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    frontend={p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
              for folder in ('fitness_app/templates','fitness_app/static') for p in (ROOT/folder).rglob('*')
              if p.is_file() and p.suffix in {'.html','.js','.css'}}
    signature=hashlib.sha256(json.dumps({'inputs':identity,'frontend':frontend,'posture':policy_version()},sort_keys=True).encode()).hexdigest()
    if OUT.exists():
        frozen=json.loads(OUT.read_text(encoding='utf-8'))
        old_inputs={k:v for k,v in frozen['inputs'].items() if k!='script'}
        actual_inputs={k:v for k,v in identity.items() if k!='script'}
        if old_inputs!=actual_inputs or frozen['posture_version']!=policy_version():
            raise ValueError('第三轮起点已冻结；当前代码/数据有变化，不能覆盖原比较基线')
        print(json.dumps({'reused':True,'frontend_changed':frozen['frontend']!=frontend,'path':str(OUT.relative_to(ROOT))}));return
    model=json.loads((ROOT/INPUTS[0]).read_text(encoding='utf-8'))
    report=json.loads((ROOT/INPUTS[1]).read_text(encoding='utf-8'))
    if report['head_sha256']!=model['sha256']:raise ValueError('校准不是当前发布分类头')
    metrics=report['validation'];actions=definitions();segments=json.loads((ROOT/INPUTS[2]).read_text(encoding='utf-8'))['segments']
    entries={r['id']:r for r in segments};capabilities=[]
    for action in actions:
        aid=action['action_id'];rules=rules_for(aid);score=metrics['per_class'][aid]
        failures=[{**r,'source_segment':entries.get(r['id'])} for r in metrics['predictions'] if r['expected']==aid and r['predicted']!=aid]
        capabilities.append({'action_id':aid,'name':action['name'],'view':action['view'],'variant':action['variant'],
            'capability_version':action['capability_version'],'counting_available':action['counting_available'],
            'count_accuracy_status':action.get('count_accuracy_status','not_validated'),'reference_rules':rules,
            'development_validation':score,'meets_round3_development_recall':score['recall'] is not None and score['recall']>=.8,
            'failures':failures,'independent_specific_error_accuracy':None})
    result={'schema_version':1,'signature':signature,'inputs':identity,'frontend':frontend,'recognition_version':model['version'],
            'posture_version':policy_version(),'action_count':len(actions),'reference_rule_count':sum(len(a['reference_rules']) for a in capabilities),
            'scope':'baseline_only_not_round3_acceptance','new_model_inferences':0,'legacy_holdout_already_observed':True,
            'development_summary':{k:v for k,v in metrics.items() if k not in ('per_class','predictions')},
            'below_80_actions':[a['action_id'] for a in capabilities if not a['meets_round3_development_recall']],
            'source_segments':len(segments),'source_segments_with_specific_error_truth':sum(s.get('specific_error_ground_truth') is True for s in segments),
            'actions':capabilities,'full_round3_validation_passed':False}
    atomic_json(OUT,result)
    print(json.dumps({k:result[k] for k in ('action_count','reference_rule_count','below_80_actions','source_segments_with_specific_error_truth','new_model_inferences')},ensure_ascii=False))


if __name__=='__main__':run()
