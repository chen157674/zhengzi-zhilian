"""复用首轮视频语义，仅对实际进入易混淆分组的验证片段做本地视觉复核。"""
import hashlib,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment,project_path
configure_environment()
import numpy as np
from fitness_app.semantic_model import SemanticPredictor
from fitness_app.video_vlm import VideoActionVLM,CONFIG
from fitness_app.video_refinement import VideoFamilyRefiner,choose_family,combine,policy_version
from fitness_app.semantic_features import feature_blocks
from fitness_app.world_features import WorldFeatures
from fitness_app.research_extract import atomic_json
from evaluate_video_vlm import frames,guided_input,pose_cache_path
from evaluate_xclip import OUT
from train_semantic_fusion import metrics_for_predictions


def run():
    data=json.loads((OUT/'semantic-video-features.json').read_text(encoding='utf-8'))
    training=json.loads((OUT/'semantic-fusion-training.json').read_text(encoding='utf-8'))
    if data['status']!='completed' or {r['id'] for r in data['entries']}!={r['id'] for r in training['identity']['consumed']}:
        raise ValueError('最新视频缓存与分类头训练范围不一致')
    model=SemanticPredictor(research=True);base=VideoActionVLM('4b',quantized=True);refiner=VideoFamilyRefiner(base);world_encoder=WorldFeatures()
    consumed={r['id']:r for r in training['identity']['consumed']}
    policy={'schema':'video-family-evaluation-1','model_revision':base.info['revision'],'video_config':CONFIG,'refinement_version':policy_version(),
            'code':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in
                    ('fitness_app/video_refinement.py','fitness_app/compact_video.py','fitness_app/video_vlm.py','fitness_app/recognition_observations.py','scripts/evaluate_video_vlm.py')}}
    rows=[];predictions=[];baseline=[];new=reused=0;latencies=[]
    for entry in data['entries']:
        if entry['partition']!='validation':continue
        with np.load(project_path(entry['cache']),allow_pickle=False) as c:
            embedding=c['embedding'].copy();visual=json.loads(str(c['prediction']));times=c['timestamps'].copy()
        world=None;visibility=0.;p=OUT/(entry['id']+'-full-world-features.npz')
        if p.exists():
            with np.load(p,allow_pickle=False) as c:world,visibility=world_encoder.extract({k:c[k] for k in ('timestamps','world','people')},times[0],times[-1])
        blocks=feature_blocks(embedding,visual,world,visibility)
        if hashlib.sha256(blocks['full'].tobytes()).hexdigest()!=consumed[entry['id']]['feature_sha256']:raise ValueError('验证特征与训练记录不符')
        primary=model.from_features(embedding,visual,world,visibility);family=choose_family(primary,visual);output=primary;review=None
        if family:
            identity={'policy':policy,'family':family,'source_sha256':entry['source_sha256'],'start_ms':entry['start_ms'],'end_ms':entry['end_ms'],'crop':entry['crop'],
                      'pose_sha256':hashlib.sha256(pose_cache_path(entry).read_bytes()).hexdigest(),'timestamps_ms':times.tolist()}
            signature=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest();path=OUT/(entry['id']+'-family-'+signature[:10]+'.json')
            if path.exists():
                cached=json.loads(path.read_text(encoding='utf-8'))
                if cached['signature']!=signature:raise ValueError('分组复核缓存身份不符')
                review=cached['review'];reused+=1
            else:
                images,actual=frames(entry);facts,detail=guided_input(entry,images,actual)
                if actual!=times.tolist() or facts!=visual['pose_observations']:raise ValueError('复核画面/观测与首轮缓存不同')
                review=refiner.review(images,actual,family,facts,detail)
                atomic_json(path,{'signature':signature,'identity':identity,'review':review});new+=1
            output=combine(primary,review,family);latencies.append(review['elapsed_ms'])
        record={'id':entry['id'],'source_sha256':entry['source_sha256'],'group':entry['group'],'expected':entry['action_id']}
        baseline.append({**record,'top':primary['top_action_id'] or 'unknown','predicted':primary['action_id'],'score':primary['score'],'margin':primary['margin']})
        predictions.append({**record,'top':output['action_id'] or primary['top_action_id'] or 'unknown','predicted':output['action_id'],'score':output['score'],'margin':output['margin']})
        rows.append({**record,'family':family,'primary':primary,'review':review,'output':output})
        if family:print(json.dumps({'id':entry['id'],'family':family,'expected':entry['action_id'],'primary':primary['action_id'],'review':review['action_id'],'final':output['action_id']}),flush=True)
    report={'schema_version':1,'policy':policy,'head_sha256':model.info['sha256'],'baseline':metrics_for_predictions(baseline),'validation':metrics_for_predictions(predictions),
            'new_secondary_inferences':new,'reused_secondary_inferences':reused,'new_primary_inferences':0,'rows':rows,'runtime_enabled':False,'test_used':False,
            'secondary_latency_ms':{'p50':float(np.median(latencies)),'p95':float(np.percentile(latencies,95))} if latencies else None}
    atomic_json(OUT/'video-family-evaluation.json',report)
    print(json.dumps({'metrics':{k:v for k,v in report['validation'].items() if k not in ('per_class','predictions')},'new_secondary':new,'reused_secondary':reused}),flush=True)


if __name__=='__main__':run()
