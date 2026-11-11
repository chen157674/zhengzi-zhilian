"""保存已训练时序编码器的LN隐藏向量和教师输出；只补缺项，不存RGB。"""
import hashlib,inspect,json,importlib.metadata
import numpy as np
from fitness_app.paths import ROOT,project_path
from fitness_app.domain_video_adviser import DomainVideoAdviser,verify_resources
from fitness_app.video_sampling import fit_pixels
from fitness_app.media import display_rgb
from fitness_app.research_extract import atomic_json
from fitness_app.source_partitions import enforce_source_policy
from scripts.temporal_expert_features import recovering_images

OUT=ROOT/'data/verification/round3'
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def fingerprint(value):return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()
def array_sha(value):return hashlib.sha256(np.asarray(value,np.float32).tobytes()).hexdigest()


def load_features(entries,*,roi=False):
    trained,manifest=verify_resources();enforce_source_policy(entries);enforce_source_policy(entries,operation='inference')
    policy={'schema':'trained-domain-ln-features-1','checkpoint_sha256':manifest['sha256'],'training_signature':manifest['signature'],
        'extract_sha256':hashlib.sha256(inspect.getsource(DomainVideoAdviser.extract_head_features).encode()).hexdigest(),
        'fit_pixels_sha256':hashlib.sha256(inspect.getsource(fit_pixels).encode()).hexdigest(),'display_sha256':hashlib.sha256(inspect.getsource(display_rgb).encode()).hexdigest(),
        'preprocessor_sha256':sha(ROOT/'data/models/xclip-base-patch32/preprocessor_config.json'),
        'versions':{k:importlib.metadata.version(k) for k in ('torch','transformers','av','Pillow')},'input':'eight exact RGB frames; no source/label tokens'}
    if roi:
        from fitness_app.upper_body_roi import crop_images
        policy['roi_code_sha256']=sha(ROOT/'fitness_app/upper_body_roi.py');policy['schema']='trained-domain-upper-roi-ln-features-1'
    policy_sha=fingerprint(policy);path=OUT/('domain-roi-adaptation-features.npz' if roi else 'domain-adaptation-features.npz');records={};vectors={};teachers={};initial_weight=initial_bias=None
    if path.exists():
        with np.load(path,allow_pickle=False) as a:
            header=json.loads(str(a['header']));matrix=a['features'].copy();probabilities=a['teachers'].copy();initial_weight=a['initial_weight'].copy();initial_bias=a['initial_bias'].copy()
        if header['policy_sha256']!=policy_sha or fingerprint(header['policy'])!=policy_sha or matrix.shape!=(len(header['records']),512) or probabilities.shape!=(len(header['records']),len(manifest['labels'])):raise ValueError('已训练域特征缓存策略/维度不同')
        if array_sha(initial_weight)!=header['initial_weight_sha256'] or array_sha(initial_bias)!=header['initial_bias_sha256']:raise ValueError('原分类头缓存损坏')
        for record,value,teacher in zip(header['records'],matrix,probabilities):
            if record['id'] in records or array_sha(value)!=record['feature_sha256'] or array_sha(teacher)!=record['teacher_sha256']:raise ValueError('域特征缓存损坏或重复')
            records[record['id']]=record;vectors[record['id']]=value;teachers[record['id']]=teacher
    fallback={};fallback_info=None
    if roi:
        unchanged=[e for e in entries if e['roi_info']['crop'] is None]
        if unchanged:
            original,reference,initial_weight,initial_bias,_,fallback_info=load_features(unchanged)
            fallback={e['id']:(v,t) for e,v,t in zip(unchanged,original,reference)}
    adviser=DomainVideoAdviser(trained,manifest,research=True);hits=0;reused=0;verified={}
    def save():
        keys=list(records);header={'policy':policy,'policy_sha256':policy_sha,'records':[records[k] for k in keys],
            'initial_weight_sha256':array_sha(initial_weight),'initial_bias_sha256':array_sha(initial_bias)}
        temporary=path.with_suffix('.npz.part');owned=False
        try:
            with temporary.open('xb') as f:owned=True;np.savez_compressed(f,header=json.dumps(header,sort_keys=True),features=np.asarray([vectors[k] for k in keys],np.float32),
                teachers=np.asarray([teachers[k] for k in keys],np.float32),initial_weight=initial_weight,initial_bias=initial_bias)
            temporary.replace(path)
        finally:
            if owned and temporary.exists():temporary.unlink()
    try:
        for count,entry in enumerate(entries,1):
            identity={k:entry[k] for k in ('source_sha256','start_ms','end_ms','crop','actual_ms')};identity['policy_sha256']=policy_sha
            if roi:identity.update(roi_info=entry['roi_info'],roi_pose_sha256=entry['roi_pose_sha256'])
            sig=fingerprint(identity);key=entry['id']
            if key in records:
                if records[key]['input_signature']!=sig:raise ValueError('同一域特征输入身份不同，不覆盖')
                hits+=1;continue
            media=project_path(entry['path'])
            if entry['path'] not in verified:verified[entry['path']]=sha(media)
            if verified[entry['path']]!=entry['source_sha256']:raise ValueError('域特征原片摘要变化')
            if key in fallback:
                feature,teacher=fallback[key];recovered=False;reused+=1
            else:
                images,recovered=recovering_images(entry,entry['actual_ms'])
                if roi:images=crop_images(images,entry['roi_info']['crop'])
                feature,teacher=adviser.extract_head_features(images)
            if initial_weight is None:
                initial_weight=adviser.net.head[2].weight.detach().float().cpu().numpy().copy();initial_bias=adviser.net.head[2].bias.detach().float().cpu().numpy().copy()
            records[key]={'id':key,'input_signature':sig,'identity':identity,'feature_sha256':array_sha(feature),'teacher_sha256':array_sha(teacher),'sequential_recovery':recovered}
            vectors[key]=feature;teachers[key]=teacher;save()
            if count%10==0 or count==len(entries):print(json.dumps({'stage':'trained_domain_features','ready':count,'total':len(entries),'hits':hits,'new_calls':adviser.calls}),flush=True)
        return (np.asarray([vectors[e['id']] for e in entries],np.float32),np.asarray([teachers[e['id']] for e in entries],np.float32),initial_weight,initial_bias,manifest['labels'],
            {'path':path.relative_to(ROOT).as_posix(),'sha256':sha(path),'policy_sha256':policy_sha,'inputs':len(entries),'cache_hits':hits,'new_encoder_calls':adviser.calls,
             'records':[records[e['id']] for e in entries],'checkpoint_sha256':manifest['sha256'],
             **({'unchanged_pixel_vectors_reused':reused,'fallback_whole_cache':fallback_info} if roi else {})})
    finally:adviser.close()
