"""冻结33类主干，训练受限胸部子类候选；完整复核/运动链路只读缓存回放。"""
import hashlib,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment,project_path
configure_environment()
import numpy as np
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_limits
from fitness_app.action_expert import CHEST,OTHER,MINIMUM_SCORE,MINIMUM_MARGIN,labels_for,refine_chest
from fitness_app.semantic_model import SemanticPredictor
from fitness_app.video_refinement import combine,policy_version as family_version
from fitness_app.motion_evidence import evaluate as motion_evaluate,apply as motion_apply
from fitness_app.research_extract import atomic_json
from scripts.partial_action_training import source_weights,assert_source_split,allowed_mask,fit_linear,probabilities,standardize_frozen
from scripts.frozen_fusion_matrix import load_matrix
from scripts.train_partial_action_fusion import FROZEN_INDEX_SHA256,FROZEN_HEAD_SHA256
from scripts.train_semantic_fusion import metrics_for_predictions

OUT=ROOT/'data/verification/round3';OLD=ROOT/'data/verification/step-22'
def read(path):return json.loads(path.read_text(encoding='utf-8'))
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def cases():
    previous=read(OUT/'partial-view-fusion-training.json');original=read(OLD/'semantic-video-features.json')
    entries={r['id']:r for r in original['entries']+read(OUT/'recognition-probe-inputs.json')['entries']+read(OUT/'partial-view-training-inputs.json')['entries']}
    training={r['id']:r for r in previous['training']}
    evaluation={r['id']:r for p in ('validation','diagnostic') for r in previous['results'][p]['predictions']}
    expected={r['id']:r for r in previous['identity']['consumed']}
    if len(expected)!=len(previous['identity']['consumed']) or set(training)&set(evaluation) or set(training)|set(evaluation)!=set(expected):raise ValueError('既有训练/评估清单不完整')
    rows=[]
    for key in expected:
        entry=entries[key];meta=training.get(key) or evaluation[key]
        if any(meta[k]!=entry[k] for k in ('source_sha256','group','action_id')) or (key in training)!=(entry['partition']=='train'):raise ValueError('原来源或分区改变')
        if key in evaluation and meta['partition']!=entry['partition']:raise ValueError('原评估分区改变')
        rows.append({**entry,'label_scope':meta['label_scope'],'training_origin':meta.get('origin')})
    assert_source_split([r for r in rows if r['partition']=='train'],[r for r in rows if r['partition']!='train'])
    observed={r['id']:r for r in read(OUT/'new-source-action-probe.json')['rows']+read(OUT/'partial-view-action-probe.json')['rows']}
    return rows,expected,observed,previous


def cached_baseline(row,predictor,families,calibration,observed):
    """只复用身份一致的视觉复核，不用新分类器推理或缺失复核冒充正式链。"""
    key=row['id']
    if row['partition']=='diagnostic':
        cached=observed[key]
        if cached['source_sha256']!=row['source_sha256'] or cached['deployed']['model_version']!=predictor.info['version']:raise ValueError('诊断缓存基准改变')
        result=dict(cached['deployed'])
    else:
        cached=families[key]
        if cached['source_sha256']!=row['source_sha256'] or cached['expected']!=row['action_id']:raise ValueError('视觉复核输入改变')
        result=dict(cached['primary'])
        if result['action_id'] is not None and cached['family'] in predictor.info['refinement_families']:
            if cached['review'] is None:raise ValueError('缺失必需的原视觉复核，不能当完整基准')
            result=combine(result,cached['review'],cached['family'])
        if result['action_id']!=calibration[key]['predicted']:raise ValueError('复用复核的输出与原发布校准不一致')
    pose=project_path(observed[key]['pose_cache']) if key in observed else OLD/(key+'-full-world-features.npz')
    semantic=project_path(observed[key]['semantic_cache']) if key in observed else project_path(row['cache'])
    with np.load(pose,allow_pickle=False) as a:arrays={k:a[k].copy() for k in ('timestamps','points','sizes','people')}
    with np.load(semantic,allow_pickle=False) as a:times=a['timestamps'].copy()
    if row['partition']!='diagnostic':result=predictor._with_motion(result,arrays,times)
    return result,arrays,times


def main(mode='train'):
    index=OLD/'semantic-video-features.json';weight=ROOT/'data/models/semantic-action-classifier.joblib';manifest=ROOT/'data/models/semantic-action-classifier.json'
    if sha(index)!=FROZEN_INDEX_SHA256 or sha(weight)!=FROZEN_HEAD_SHA256:raise ValueError('生产识别基线改变')
    rows,expected,observed,previous=cases();matrix,cache=load_matrix(rows,expected,observed)
    if mode=='cache':print(json.dumps(cache),flush=True);return
    training=[i for i,r in enumerate(rows) if r['partition']=='train'];evaluation=[i for i,r in enumerate(rows) if r['partition']!='train']
    classes=sorted([*CHEST,OTHER]);fit_rows=[]
    for i in training:
        row=rows[i];allowed=labels_for(row);bucket=row['action_id'] if row['action_id'] in CHEST else OTHER
        fit_rows.append({**row,'action_id':bucket,'label_scope':{'allowed_actions':allowed}})
    weights=source_weights(fit_rows);mask=allowed_mask(fit_rows,classes,True);scaler=StandardScaler().fit(matrix[training]);scaled=scaler.transform(matrix)
    fit_identity={'feature_signature':cache['identity_signature'],'training_ids':[rows[i]['id'] for i in training],
        'allowed_sets':[r['label_scope']['allowed_actions'] for r in fit_rows],'weights':weights.tolist(),'classes':classes,'C':.3,'max_iter':200}
    target=OUT/'chest-expert-candidate.npz';fit_code=sha(Path(__file__))
    with threadpool_limits(limits=4):
        if mode=='verify':
            with np.load(target,allow_pickle=False) as a:
                if json.loads(str(a['fit_identity']))!=fit_identity:raise ValueError('胸部候选不是相同冻结拟合输入')
                np.testing.assert_array_equal(scaler.mean_,a['mean']);np.testing.assert_array_equal(scaler.scale_,a['scale'])
                model={'coef':a['coef'].copy(),'intercept':a['intercept'].copy(),'optimization':json.loads(str(a['optimization']))};fit_code=str(a['fit_code_sha256'])
        else:model=fit_linear(scaled[training],mask,weights)
        scores=probabilities(scaled[evaluation],model)
    temporary=target.with_suffix('.npz.part');owned=False
    try:
        with temporary.open('xb') as f:owned=True;np.savez_compressed(f,classes=np.asarray(classes),mean=scaler.mean_,scale=scaler.scale_,coef=model['coef'],intercept=model['intercept'],
            fit_identity=json.dumps(fit_identity,sort_keys=True),optimization=json.dumps(model['optimization'],sort_keys=True),fit_code_sha256=fit_code,runtime_enabled=False)
        temporary.replace(target)
    finally:
        if owned and temporary.exists():temporary.unlink()
    with np.load(target,allow_pickle=False) as a:
        with threadpool_limits(limits=4):restored=probabilities(standardize_frozen(matrix[evaluation],a['mean'],a['scale']),{'coef':a['coef'],'intercept':a['intercept']})
        np.testing.assert_allclose(restored,scores,atol=1e-12,rtol=0)
    predictor=SemanticPredictor();family_report=read(OLD/'video-family-evaluation.json');release=read(OLD/'video-release-calibration.json')
    if family_report['head_sha256']!=predictor.info['sha256'] or release['head_sha256']!=predictor.info['sha256'] or family_report['policy']['refinement_version']!=family_version():raise ValueError('原视觉复核缓存策略或权重不同')
    if release['selected_families']!=predictor.info['refinement_families'] or release['require_primary_recognized'] is not True:raise ValueError('原复核选择改变')
    families={r['id']:r for r in family_report['rows']};calibration={r['id']:r for r in release['validation']['predictions']}
    version='experimental-chest-expert-'+sha(target)[:12];outputs=[];protected=0
    for i,values in zip(evaluation,scores):
        row=rows[i];base,arrays,times=cached_baseline(row,predictor,families,calibration,observed)
        order=np.argsort(values)[::-1];action=classes[order[0]];score=float(values[order[0]]);gap=score-float(values[order[1]])
        candidate=refine_chest(base,action,score,gap,version)
        if base.get('action_id') not in CHEST:
            if candidate!=base:raise ValueError('子类复核改变了未知/非胸部基准')
            protected+=1
        if candidate!=base:
            evidence=motion_evaluate(arrays,int(times[0]),int(times[-1]),candidate['action_id'],predictor.info['motion_evidence']['config']);candidate=motion_apply(candidate,evidence)
        outputs.append({k:row[k] for k in ('id','source_sha256','group','partition')}|{'expected':row['action_id'],'baseline':base,'candidate':candidate,
            'expert_top':action,'expert_score':score,'expert_margin':gap,'changed':candidate['action_id']!=base['action_id']})
    results={}
    for part in ('validation','diagnostic'):
        items=[r for r in outputs if r['partition']==part];result={}
        for name in ('baseline','candidate'):
            predictions=[{'id':r['id'],'source_sha256':r['source_sha256'],'group':r['group'],'expected':r['expected'],'top':r[name].get('top_action_id') or 'unknown',
                'predicted':r[name]['action_id'],'score':r[name]['score'],'margin':r[name]['margin']} for r in items]
            result[name]=metrics_for_predictions(predictions)
        results[part]=result
    base_correct=round(results['validation']['baseline']['accuracy_including_rejections']*193)
    if base_correct!=read(OUT/'motion-evidence-release.json')['raw_original_protocol']['correct_after']:raise ValueError('完整基准回放未重现原运动保护成绩')
    failures=[]
    after=results['validation']['candidate'];before=results['validation']['baseline']
    if not model['optimization']['stationarity_passed']:failures.append('optimization stationarity below required quality')
    for action,m in after['per_class'].items():
        if m['recall'] is None or m['recall']<.8:failures.append(action+': recall below 80 percent or missing')
        if m['recall'] is not None and before['per_class'][action]['recall'] is not None and m['recall']<before['per_class'][action]['recall']:failures.append(action+': regression')
    if after['negative_false_accepts']>before['negative_false_accepts']:failures.append('negative false accepts increased')
    failures.append('development/diagnostic inputs already observed; independent classification and specific-error validation still pending')
    if sha(index)!=FROZEN_INDEX_SHA256 or sha(weight)!=FROZEN_HEAD_SHA256:raise ValueError('运行期间生产基线改变')
    report={'schema_version':1,'scope':'frozen main classifier plus experimental chest-only expert; exact cached production visual-family review and motion guard; no new GPU inference',
        'identity':{'source_training_report_sha256':sha(OUT/'partial-view-fusion-training.json'),'production_manifest_sha256':sha(manifest),'production_weight_sha256':sha(weight),
            'family_cache_sha256':sha(OLD/'video-family-evaluation.json'),'calibration_sha256':sha(OLD/'video-release-calibration.json'),'trainer_sha256':sha(Path(__file__)),
            'expert_code_sha256':sha(ROOT/'fitness_app/action_expert.py'),'matrix':cache},
        'fit_identity':fit_identity,'optimization':model['optimization'],'train_clips':len(training),'evaluation_clips':len(evaluation),'expert_classes':classes,
        'parameters':{'minimum_score':MINIMUM_SCORE,'minimum_margin':MINIMUM_MARGIN,'C':.3,'max_iter':200,'thresholds_tuned_on_these_results':False},
        'candidate_path':target.relative_to(ROOT).as_posix(),'candidate_sha256':sha(target),'candidate_version':version,'results':results,'rows':outputs,
        'protected_unknown_or_nonchest_inputs':protected,'changed_cases':sum(r['changed'] for r in outputs),'serialization_clips_verified':len(evaluation),
        'new_pose_inferences':0,'new_semantic_video_inferences':0,'new_visual_family_reviews':0,'new_training_runs':0 if mode=='verify' else 1,
        'runtime_enabled':False,'gate_passed':not failures,'gate_failures':failures,
        'limitations':['Seven-class cluster includes reverse pec deck as a visual confuser, not a training-body-part recommendation.',
            'Unknown and nonchest BASE outputs are unchanged; this does not prove their correctness.','New training windows remain training-only; no new independent evaluation participants.',
            'Expert scores are uncalibrated; no clinical or posture-error quality conclusion.']}
    atomic_json(OUT/'chest-expert-training.json',report)
    print(json.dumps({'train_clips':len(training),'validation_clips':results['validation']['baseline']['positive_clips']+results['validation']['baseline']['negative_clips'],
        'baseline_correct':base_correct,'candidate_correct':round(after['accuracy_including_rejections']*193),'changed_cases':report['changed_cases'],
        'bench':after['per_class']['dumbbell_bench_press'],'pec_deck':after['per_class']['pec_deck_fly'],'protected_inputs':protected,
        'gate_passed':report['gate_passed'],'runtime_enabled':False,'matrix_cache':cache['cache_hit']},ensure_ascii=False),flush=True)


if __name__=='__main__':
    if sys.argv[1:]==['--check-feature-cache']:main('cache')
    elif sys.argv[1:]==['--verify-candidate']:main('verify')
    elif sys.argv[1:]:raise SystemExit('支持无参数拟合、--check-feature-cache或--verify-candidate')
    else:main()
