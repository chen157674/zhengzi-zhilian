"""只训练本项目动作融合层；复用昂贵视频语义/骨架缓存，不重跑GPU视频推理。"""
import argparse,collections,hashlib,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment,project_path
configure_environment()
import numpy as np,joblib
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import ExtraTreesClassifier
from threadpoolctl import threadpool_limits
from fitness_app.semantic_features import feature_blocks,VERSION
from fitness_app.video_vlm import LABELS
from fitness_app.world_features import WorldFeatures
from fitness_app.research_extract import atomic_json

OUT=ROOT/'data/verification/step-22'
DIRECTORY=ROOT/'data/models'
REPORT=OUT/'semantic-fusion-training.json'


def evaluate(entries,scores,classes,minimum=.5,margin=.15,accept_mask=None):
    predictions=[]
    for index,(entry,values) in enumerate(zip(entries,scores)):
        order=np.argsort(values)[::-1];top=str(classes[order[0]]);score=float(values[order[0]]);difference=score-float(values[order[1]])
        predictions.append({'id':entry['id'],'source_sha256':entry['source_sha256'],'group':entry['group'],'expected':entry['action_id'],
                            'top':top,'predicted':top if top!='unknown' and score>=minimum and difference>=margin and (accept_mask is None or accept_mask[index]) else None,'score':score,'margin':difference})
    return metrics_for_predictions(predictions)


def metrics_for_predictions(predictions):
    positive=[r for r in predictions if r['expected']!='unknown'];negative=[r for r in predictions if r['expected']=='unknown'];per_class={}
    for action in [a for a in LABELS if a]:
        cases=[r for r in positive if r['expected']==action];accepted=[r for r in predictions if r['predicted']==action]
        per_class[action]={'clips':len(cases),'original_videos':len({r['source_sha256'] for r in cases}),'groups':len({r['group'] for r in cases}),
                           'recall':sum(r['predicted']==action for r in cases)/len(cases) if cases else None,
                           'top1':sum(r['top']==action for r in cases)/len(cases) if cases else None,
                           'precision':sum(r['expected']==action for r in accepted)/len(accepted) if accepted else None}
    covered=[r for r in predictions if r['predicted'] is not None]
    return {'positive_clips':len(positive),'accuracy_including_rejections':sum(r['predicted']==r['expected'] for r in positive)/max(1,len(positive)),
            'top1':sum(r['top']==r['expected'] for r in positive)/max(1,len(positive)),
            'macro_recall_all_33':sum(r['recall'] or 0 for r in per_class.values())/33,
            'coverage':sum(r['predicted'] is not None for r in positive)/max(1,len(positive)),
            'accepted_precision':sum(r['predicted']==r['expected'] for r in covered)/len(covered) if covered else None,
            'negative_clips':len(negative),'negative_false_accepts':sum(r['predicted'] is not None for r in negative),
            'per_class':per_class,'predictions':predictions}


def run():
    previous=json.loads(REPORT.read_text(encoding='utf-8')) if REPORT.exists() else None
    previous_info=json.loads((DIRECTORY/'semantic-action-classifier.json').read_text(encoding='utf-8')) if (DIRECTORY/'semantic-action-classifier.json').exists() else None
    data=json.loads((OUT/'semantic-video-features.json').read_text(encoding='utf-8'))
    if data['status']!='completed':raise ValueError('视频语义尚未全部完成，不把小类缺失的部分结果当成完整训练')
    entries=data['entries'];groups={p:{r['group'] for r in entries if r['partition']==p} for p in ('train','validation')}
    if groups['train']&groups['validation']:raise ValueError('融合训练/验证来源组交叉')
    block_names=('semantic','visual','geometry','full');blocks={k:[] for k in block_names};consumed=[];world_encoder=WorldFeatures()
    for entry in entries:
        with np.load(project_path(entry['cache']),allow_pickle=False) as cache:
            if str(cache['signature'])!=entry['feature_signature']:raise ValueError('语义缓存身份不一致')
            embedding=cache['embedding'].copy();prediction=json.loads(str(cache['prediction']));times=cache['timestamps']
        world=None;visibility=0.;world_path=OUT/(entry['id']+'-full-world-features.npz')
        if world_path.exists():
            with np.load(world_path,allow_pickle=False) as cache:
                source=json.loads(str(cache['identity']))
                if any(source[key]!=entry[key] for key in ('source_sha256','start_ms','end_ms','crop')):raise ValueError('世界坐标缓存与视频片段不一致')
                world,visibility=world_encoder.extract({k:cache[k] for k in ('timestamps','world','people')},times[0],times[-1])
        features=feature_blocks(embedding,prediction,world,visibility)
        for key in block_names:blocks[key].append(features[key])
        consumed.append({'id':entry['id'],'source_sha256':entry['source_sha256'],'partition':entry['partition'],'action':entry['action_id'],
                         'feature_sha256':hashlib.sha256(features['full'].tobytes()).hexdigest()})
    blocks={k:np.asarray(v,np.float32) for k,v in blocks.items()};train=np.asarray([i for i,r in enumerate(entries) if r['partition']=='train']);valid=np.asarray([i for i,r in enumerate(entries) if r['partition']=='validation'])
    training=[entries[i] for i in train];validation=[entries[i] for i in valid];labels=[r['action_id'] for r in training]
    required={a or 'unknown' for a in LABELS}
    if set(labels)!=required:raise ValueError('融合层缺少训练类别: '+str(required-set(labels)))
    # 多个裁切/时间段仍只算一个原始视频的权重，不让重复片段冒充更多人物。
    counts=collections.Counter((r['action_id'],r['source_sha256']) for r in training);per_class=collections.Counter(a for a,s in counts)
    weights=np.asarray([1/(counts[(r['action_id'],r['source_sha256'])]*per_class[r['action_id']]) for r in training]);weights*=len(weights)/weights.sum()
    candidates=[];scores_by_name={};estimators={}
    with threadpool_limits(limits=4):
        for block in ('semantic','visual','full'):
            for strength in (.03,.3,3.):
                name=f'{block}_linear_C{strength}'
                estimator=make_pipeline(StandardScaler(),LogisticRegression(C=strength,max_iter=2000,random_state=20261023))
                estimator.fit(blocks[block][train],labels,logisticregression__sample_weight=weights)
                probabilities=estimator.predict_proba(blocks[block][valid]);classes=list(estimator.classes_)
                estimators[name]=(block,estimator);scores_by_name[name]=probabilities
        # 语义向量的方向本身有意义；保留不逐列放大噪声的对照，尤其照顾少样本器械类别。
        for strength in (300.,3000.):
            name=f'semantic_direction_linear_C{strength}'
            estimator=LogisticRegression(C=strength,max_iter=2000,random_state=20261023)
            estimator.fit(blocks['semantic'][train],labels,sample_weight=weights)
            estimators[name]=('semantic',estimator);scores_by_name[name]=estimator.predict_proba(blocks['semantic'][valid])
        for block in ('geometry','full'):
            name=block+'_extra_trees'
            estimator=ExtraTreesClassifier(n_estimators=320,min_samples_leaf=1,max_features=.5,random_state=20261023,n_jobs=4)
            estimator.fit(blocks[block][train],labels,sample_weight=weights)
            estimators[name]=(block,estimator);scores_by_name[name]=estimator.predict_proba(blocks[block][valid]);classes=list(estimator.classes_)
    if any(list(estimator.classes_)!=classes for _,estimator in estimators.values()):raise ValueError('融合候选标签顺序不同')
    best=None
    for name,probabilities in scores_by_name.items():
        for minimum,margin in ((.35,.05),(.5,.15),(.65,.2)):
            metrics=evaluate(validation,probabilities,classes,minimum,margin)
            score=metrics['macro_recall_all_33']-.1*metrics['negative_false_accepts']
            row={'name':name,'minimum_score':minimum,'minimum_margin':margin,'metrics':metrics,'selection_score':score}
            candidates.append({k:v for k,v in row.items() if k!='metrics'}|{'metrics':{k:v for k,v in metrics.items() if k not in ('per_class','predictions')}})
            if best is None or score>best['selection_score']:best=row
    selected_block,selected=estimators[best['name']];path=DIRECTORY/'semantic-action-classifier.joblib'
    joblib.dump(selected,path,compress=3)
    identity={'encoder_policy_sha256':data['policy_sha256'],'consumed':consumed,'features_version':VERSION,'classes':classes,
              'world_encoder_sha256':json.loads((ROOT/'data/models/skelgym/world-features.json').read_text())['sha256']}
    signature=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest();metrics=best['metrics']
    passed=bool(metrics['accuracy_including_rejections']>=.7 and metrics['macro_recall_all_33']>=.7 and metrics['coverage']>=.8 and metrics['accepted_precision']>=.85 and
                metrics['negative_false_accepts']==0 and all(r['recall'] is not None and r['recall']>=.7 for r in metrics['per_class'].values()))
    info={'schema_version':1,'version':'semantic-fusion-'+signature[:12],'signature':signature,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
          'encoder_policy_sha256':data['policy_sha256'],'encoder_policy':data['policy'],'features_version':VERSION,'block':selected_block,
          'features_code_sha256':hashlib.sha256((ROOT/'fitness_app/semantic_features.py').read_bytes()).hexdigest(),
          'world_encoder_sha256':identity['world_encoder_sha256'],
          'world_features_code_sha256':hashlib.sha256((ROOT/'fitness_app/world_features.py').read_bytes()).hexdigest(),
          'minimum_score':best['minimum_score'],'minimum_margin':best['minimum_margin'],'classes':classes,'exercise_classes':33,
          'runtime_enabled':False,'development_gate_passed':passed,'release_gate_passed':False}
    atomic_json(path.with_suffix('.json'),info)
    history=previous.get('previous_runs',[]) if previous else []
    if previous and previous.get('identity')!=identity:
        history=history+[{'identity':previous['identity'],'model':previous_info,'selected':previous['selected'],'train_clips':previous['train_clips'],
                          'validation_clips':previous['validation_clips'],'validation':previous['validation']}]
    atomic_json(REPORT,{'schema_version':1,'identity':identity,'selected':best['name'],'validation':metrics,'candidates':candidates,'previous_runs':history,
                        'train_clips':len(train),'validation_clips':len(valid),'development_gate_passed':passed,'runtime_enabled':False,'test_used':False,
                        'selection':'development source-separated macro recall with false-positive penalty; no heldout data read; all low-confidence/rejected positives in denominator'})
    print(json.dumps({'selected':best['name'],'passed':passed,'metrics':{k:v for k,v in metrics.items() if k not in ('per_class','predictions')}}),flush=True)


if __name__=='__main__':run()
