"""只读既有健身域训练权重，按原固定门槛验证其对胸部误分的互补性。"""
import hashlib,inspect,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment,project_path
configure_environment()
import numpy as np
from fitness_app.domain_video_adviser import verify_resources,reusable_prediction,DomainVideoAdviser
from fitness_app.action_expert import CHEST,MINIMUM_SCORE,MINIMUM_MARGIN,refine_chest
from fitness_app.source_partitions import enforce_source_policy
from fitness_app.semantic_model import SemanticPredictor
from fitness_app.video_sampling import sample_frames,fit_pixels
from fitness_app.media import display_rgb
from fitness_app.content_bounds import detect_content_bounds
from fitness_app.motion_evidence import evaluate as motion_evaluate,apply as motion_apply
from fitness_app.research_extract import atomic_json
from scripts.temporal_expert_features import recovering_images
from scripts.train_chest_expert import cases,cached_baseline,read,sha,OUT,OLD
from scripts.train_semantic_fusion import metrics_for_predictions
from scripts.train_partial_action_fusion import FROZEN_INDEX_SHA256,FROZEN_HEAD_SHA256


def fingerprint(value):return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()


def main(cache_only=False):
    if sha(OLD/'semantic-video-features.json')!=FROZEN_INDEX_SHA256 or sha(ROOT/'data/models/semantic-action-classifier.joblib')!=FROZEN_HEAD_SHA256:raise ValueError('原主干基线改变')
    trained,manifest=verify_resources();rows,expected,observed,_=cases();evaluation=[i for i,r in enumerate(rows) if r['partition']!='train']
    old_training=[r for r in trained['identity']['inputs'] if r['partition']=='train'];enforce_source_policy(old_training)
    if {r['group'] for r in old_training}&{rows[i]['group'] for i in evaluation} or {r['source_sha256'] for r in old_training}&{rows[i]['source_sha256'] for i in evaluation}:raise ValueError('已有候选训练与当前评估来源重叠')
    enforce_source_policy([rows[i] for i in evaluation],operation='inference')
    predictor=SemanticPredictor();family=read(OLD/'video-family-evaluation.json');release=read(OLD/'video-release-calibration.json')
    if family['head_sha256']!=predictor.info['sha256'] or release['head_sha256']!=predictor.info['sha256']:raise ValueError('原复核缓存权重不同')
    families={r['id']:r for r in family['rows']};calibration={r['id']:r for r in release['validation']['predictions']}
    baseline={i:cached_baseline(rows[i],predictor,families,calibration,observed) for i in evaluation};routed=[i for i in evaluation if baseline[i][0]['action_id'] in CHEST]
    plan={'schema_version':1,'scope':'fixed chest-only adviser from existing supervised video checkpoint; no retraining or new holdout reads',
        'checkpoint_sha256':manifest['sha256'],'primary_weight_sha256':predictor.info['sha256'],'minimum_score':MINIMUM_SCORE,'minimum_margin':MINIMUM_MARGIN,
        'evaluation_ids':[rows[i]['id'] for i in evaluation],'routed_ids':[rows[i]['id'] for i in routed]}
    plan_path=OUT/'domain-adviser-plan.json'
    if plan_path.exists():
        if read(plan_path)!=plan:raise ValueError('冻结健身域顾问计划不同，不覆盖')
    else:atomic_json(plan_path,plan)
    policy={'checkpoint_sha256':manifest['sha256'],'training_signature':trained['signature'],
        'sampling_code_sha256':sha(ROOT/'fitness_app/video_sampling.py'),'display_code_sha256':hashlib.sha256(inspect.getsource(display_rgb).encode()).hexdigest(),
        'bounds_code_sha256':hashlib.sha256(inspect.getsource(detect_content_bounds).encode()).hexdigest(),
        'preprocessor_sha256':sha(ROOT/'data/models/xclip-base-patch32/preprocessor_config.json'),'precision':'fp32_weights_bfloat16_autocast','frames':8}
    policy_sha=fingerprint(policy);cache_path=OUT/'domain-adviser-output-cache.json';cache={'schema_version':1,'policy':policy,'policy_sha256':policy_sha,'rows':[],'legacy_check':None}
    if cache_path.exists():
        cache=read(cache_path)
        if cache['policy_sha256']!=policy_sha or fingerprint(cache['policy'])!=policy_sha:raise ValueError('健身域顾问缓存策略不同')
    cached={r['id']:r for r in cache['rows']};old_inputs={r['id']:r for r in trained['identity']['inputs']};old_predictions={r['id']:r for r in trained['validation']['predictions']}
    adviser=DomainVideoAdviser(trained,manifest,research=True);reused=hits=fresh=0;records=[];verified_media={}
    try:
        for i in routed:
            entry=rows[i];key=entry['id'];source=project_path(entry['path'])
            if entry['path'] not in verified_media:verified_media[entry['path']]=sha(source)
            if verified_media[entry['path']]!=entry['source_sha256']:raise ValueError('原视频摘要改变')
            semantic=project_path(observed[key]['semantic_cache']) if key in observed else project_path(entry['cache'])
            if sha(semantic)!=expected[key]['semantic_cache_sha256']:raise ValueError('冻结语义缓存改变')
            with np.load(semantic,allow_pickle=False) as a:desired=a['timestamps'].astype(np.int64).tolist()
            identity={k:entry[k] for k in ('source_sha256','start_ms','end_ms','crop')};identity.update(actual_ms=desired,policy_sha256=policy_sha)
            identity_sha=fingerprint(identity)
            if key in cached:
                record=cached[key]
                if record['input_signature']!=identity_sha:raise ValueError('顾问缓存输入身份改变')
                hits+=1;records.append(record);continue
            images,actual=sample_frames(entry);aligned=actual==desired
            if not aligned:images,_=recovering_images(entry,desired)
            prior=old_predictions.get(key);can_reuse=reusable_prediction(entry,old_inputs.get(key),prior,actual,desired)
            if can_reuse:
                prediction={k:prior[k] for k in ('top','score','margin')};origin='original_supervised_validation_cache';reused+=1
                will_change=refine_chest(baseline[i][0],prediction['top'],prediction['score'],prediction['margin'],'pilot')!=baseline[i][0]
                if cache['legacy_check'] is None and will_change:
                    new=adviser.predict(images);score_error=abs(new['score']-prediction['score']);margin_error=abs(new['margin']-prediction['margin'])
                    old_gate=prediction['score']>=MINIMUM_SCORE and prediction['margin']>=MINIMUM_MARGIN;new_gate=new['score']>=MINIMUM_SCORE and new['margin']>=MINIMUM_MARGIN
                    if new['top']!=prediction['top'] or old_gate!=new_gate or score_error>.005 or margin_error>.01:raise ValueError('既有视频顾问的加载复验不等价，不能沿用旧输出')
                    cache['legacy_check']={'id':key,'top_matches':True,'gate_matches':True,'score_error':score_error,'margin_error':margin_error,
                        'score_tolerance':.005,'margin_tolerance':.01,'reason':'same checkpoint and sampled frames; bf16 evaluation batch4 versus batch1'}
            else:prediction=adviser.predict(images);origin='new_checkpoint_inference';fresh+=1
            pixel_digest=hashlib.sha256(fit_pixels(images,adviser.config).astype(np.float16).tobytes()).hexdigest()
            record={'id':key,'source_sha256':entry['source_sha256'],'input_signature':identity_sha,'identity':identity,'prediction':prediction,'origin':origin,
                'pixel_sha256':pixel_digest,'old_sampling_aligned':aligned,'original_cache_reused':can_reuse}
            cached[key]=record;records.append(record);cache['rows']=list(cached.values());atomic_json(cache_path,cache)
        if cache_only:
            print(json.dumps({'consulted':len(routed),'cache_hits':hits,'new_model_calls':adviser.calls}),flush=True);return
        by={r['id']:r for r in records};outputs=[];version='experimental-domain-adviser-'+fingerprint({'weights':manifest['sha256'],'plan':plan})[:12]
        for i in evaluation:
            entry=rows[i];base,arrays,times=baseline[i];record=by.get(entry['id']);candidate=dict(base)
            if record:
                p=record['prediction'];candidate=refine_chest(base,p['top'],p['score'],p['margin'],version)
                if candidate!=base:candidate=motion_apply(candidate,motion_evaluate(arrays,int(times[0]),int(times[-1]),candidate['action_id'],predictor.info['motion_evidence']['config']))
            elif candidate!=base:raise ValueError('未咨询顾问的原输出被改变')
            outputs.append({k:entry[k] for k in ('id','source_sha256','group','partition')}|{'expected':entry['action_id'],'baseline':base,'candidate':candidate,
                'adviser_invoked':record is not None,'adviser_prediction':record['prediction'] if record else None,'changed':base['action_id']!=candidate['action_id']})
        results={}
        for part in ('validation','diagnostic'):
            items=[r for r in outputs if r['partition']==part];results[part]={}
            for name in ('baseline','candidate'):
                predictions=[{'id':r['id'],'source_sha256':r['source_sha256'],'group':r['group'],'expected':r['expected'],'top':r[name].get('top_action_id') or 'unknown',
                    'predicted':r[name]['action_id'],'score':r[name]['score'],'margin':r[name]['margin']} for r in items]
                results[part][name]=metrics_for_predictions(predictions)
        before=results['validation']['baseline'];after=results['validation']['candidate'];base_correct=round(before['accuracy_including_rejections']*193)
        if base_correct!=169:raise ValueError('未重现原完整基准')
        failures=[]
        for action,value in after['per_class'].items():
            prior=before['per_class'][action]
            if value['recall'] is None or value['recall']<.8:failures.append(action+': below 80 percent or missing')
            if value['recall'] is not None and prior['recall'] is not None and value['recall']<prior['recall']:failures.append(action+': regression')
            if value['original_videos']<3 or value['groups']<2:failures.append(action+': source coverage insufficient')
        if after['negative_false_accepts']>before['negative_false_accepts']:failures.append('negative false accepts increased')
        if results['diagnostic']['candidate']['accuracy_including_rejections']<results['diagnostic']['baseline']['accuracy_including_rejections']:failures.append('diagnostic regression')
        failures.append('full R3 independent classification, specific-error, real AI and phone acceptance pending')
        report={'schema_version':1,'scope':'existing trained full-frame video checkpoint as fixed chest adviser; exact original all33 evaluation population, no new training',
            'identity':{'checkpoint_sha256':manifest['sha256'],'training_signature':trained['signature'],'plan_sha256':sha(plan_path),'output_cache_sha256':sha(cache_path),
                'primary_manifest_sha256':sha(ROOT/'data/models/semantic-action-classifier.json'),'primary_weight_sha256':sha(ROOT/'data/models/semantic-action-classifier.joblib'),
                'evaluator_sha256':sha(Path(__file__)),'adviser_code_sha256':sha(ROOT/'fitness_app/domain_video_adviser.py')},
            'legacy_reused':reused,'cache_hits':hits,'fresh_predictions':fresh,'new_model_calls':adviser.calls,'new_training_runs':0,'new_pose_calls':0,'new_qwen_calls':0,
            'legacy_check':cache['legacy_check'],'all_evaluation':len(evaluation),'consulted':len(routed),'passthrough':len(evaluation)-len(routed),'rows':outputs,'results':results,
            'changed_cases':sum(r['changed'] for r in outputs),'runtime_enabled':False,'gate_passed':not failures,'gate_failures':failures,
            'limitations':['This checkpoint was selected on old development labels; reuse is not independent validation.','A single corrected dumbbell bench case does not establish class recall on new people.',
                'The full-frame fit transform differs from centre-cropped features; this is not a controlled causal proof of which change helped.','No new reserved publisher or specific-error labels used.']}
        output=OUT/'domain-adviser-evaluation.json'
        if output.exists():
            prior=read(output);report['initial_run']=prior.get('initial_run') or {k:prior[k] for k in ('legacy_reused','fresh_predictions','new_model_calls','legacy_check')}
        atomic_json(output,report)
        print(json.dumps({'baseline_correct':base_correct,'candidate_correct':round(after['accuracy_including_rejections']*193),'bench':after['per_class']['dumbbell_bench_press'],
            'changed':report['changed_cases'],'legacy_reused':reused,'new_model_calls':adviser.calls,'gate_passed':report['gate_passed'],'runtime_enabled':False},ensure_ascii=False),flush=True)
    finally:adviser.close()


if __name__=='__main__':
    if sys.argv[1:]==['--check-cache']:main(True)
    elif sys.argv[1:]:raise SystemExit('支持无参数评估或--check-cache')
    else:main()
