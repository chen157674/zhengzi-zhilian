"""冻结基础发布候选；保留逐类严格门槛，不把开发总分冒充全部动作正式通过。"""
import hashlib,json,sys,itertools
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment
configure_environment()
from fitness_app.video_refinement import policy_version
from fitness_app.research_extract import atomic_json
from train_semantic_fusion import metrics_for_predictions,OUT


def run():
    report=json.loads((OUT/'video-family-evaluation.json').read_text(encoding='utf-8'))
    path=ROOT/'data/models/semantic-action-classifier.json';info=json.loads(path.read_text(encoding='utf-8'))
    if report['head_sha256']!=info['sha256']:raise ValueError('复核评估并非当前分类头')
    candidates=[]
    for families in ([],['shoulder'],['cable','shoulder'],['cable','chest','shoulder']):
        predictions=[]
        for row in report['rows']:
            # 原分类器拒判时保持拒判；不能靠二次提问把准备、讲解或证据不足强行变成训练。
            output=row['output'] if row['family'] in families and row['primary']['action_id'] is not None else row['primary']
            predictions.append({k:row[k] for k in ('id','expected','source_sha256','group')}|{
                'predicted':output['action_id'],'top':output.get('top_action_id') or 'unknown','score':output['score'],'margin':output['margin']})
        metrics=metrics_for_predictions(predictions)
        candidates.append({'families':families,'validation':metrics,'selection_score':metrics['macro_recall_all_33']-.1*metrics['negative_false_accepts']})
    best=max(candidates,key=lambda r:r['selection_score']);metrics=best['validation']
    passed=bool(metrics['accuracy_including_rejections']>=.7 and metrics['macro_recall_all_33']>=.7 and metrics['coverage']>=.8 and
                metrics['accepted_precision']>=.85 and metrics['negative_false_accepts']==0)
    calibration={'schema_version':1,'head_sha256':info['sha256'],'refinement_policy':report['policy'],'selected_families':best['families'],
                 'require_primary_recognized':True,'validation':metrics,'basic_development_gate_passed':passed,
                 'full_development_gate_passed':info['development_gate_passed'],'candidates':candidates,'test_used':False,
                 'scope':'Development selection only; all rejected positives count as failures. Basic overall/macro gate differs from every-class/full validation. Independent heldout/pipeline checks still required.'}
    target=OUT/'video-release-calibration.json';atomic_json(target,calibration)
    info.update(refinement_enabled=bool(best['families']),refinement_families=best['families'],refinement_require_primary=True,
                refinement_policy_version=policy_version(),refinement_code_sha256=hashlib.sha256((ROOT/'fitness_app/video_refinement.py').read_bytes()).hexdigest(),
                refinement_compact_code_sha256=hashlib.sha256((ROOT/'fitness_app/compact_video.py').read_bytes()).hexdigest(),
                calibration_sha256=hashlib.sha256(target.read_bytes()).hexdigest(),basic_development_gate_passed=passed,
                runtime_cpu_threads=4,
                full_validation_passed=False,release_mode='basic_trained_video',runtime_enabled=False,release_gate_passed=False)
    info['version']='semantic-fusion-'+info['signature'][:12]+'-'+info['calibration_sha256'][:10]
    atomic_json(path,info)
    print(json.dumps({'selected':best['families'],'basic_development_gate_passed':passed,'runtime_enabled':False,
                      'metrics':{k:v for k,v in metrics.items() if k not in ('per_class','predictions')}}),flush=True)


if __name__=='__main__':run()
