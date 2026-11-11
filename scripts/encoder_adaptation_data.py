"""视频编码器适配的冻结输入和原教师缓存；不重复旧134个编码，不读取预留来源。"""
import hashlib,inspect,json
import numpy as np
from fitness_app.paths import ROOT,project_path
from fitness_app.video_encoder_adaptation import route_needed
from fitness_app.video_sampling import fit_pixels
from fitness_app.video_sampling import sample_frames
from fitness_app.domain_video_adviser import reusable_prediction
from fitness_app.source_partitions import enforce_source_policy
from fitness_app.research_extract import atomic_json
from scripts.train_chest_expert import cases,read,sha,OUT
from scripts.partial_action_training import assert_source_split
from scripts.temporal_expert_features import recovering_images
from scripts.domain_adaptation_features import load_features


def fingerprint(value):return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()


def inputs():
    rows,expected,observed,_=cases();lookup={r['id']:r for r in rows};prepared=read(OUT/'new-cc-reviewed-inputs.json')
    if sha(OUT/'new-cc-active-inputs.json')!=prepared['frame_review_sha256']:raise ValueError('Joe原窗口审核身份变化')
    for r in prepared['entries']:
        if r['eligible_training']:lookup[r['id']]=r
    training=[]
    for meta in read(OUT/'domain-head-adaptation-plan.json')['training']:
        row=lookup[meta['id']]
        if any(row[k]!=meta[k] for k in ('source_sha256','group')):raise ValueError('既有训练来源改变')
        training.append(dict(row,actual_ms=meta['actual_ms'],label_scope=meta['label_scope']))
    extra=read(OUT/'encoder-adaptation-inputs.json')
    if extra['existing_prepared_inputs_sha256']!=sha(OUT/'new-cc-reviewed-inputs.json') or extra['contact_sheet_sha256']!=sha(project_path(extra['contact_sheet'])):raise ValueError('新增弯举审核身份变化')
    for row in extra['training']:
        if row.get('developer_review',{}).get('classification_label_retained') is not True:raise ValueError('新增窗口未经实际帧复核')
        training.append(dict(row))
    if len({r['id'] for r in training})!=len(training):raise ValueError('训练窗口重复')
    evaluation=[dict(r) for r in rows if r['partition']!='train'];parent=read(OUT/'domain-adviser-evaluation.json');by={r['id']:r for r in parent['rows']}
    if set(by)!={r['id'] for r in evaluation}:raise ValueError('原完整评估人口不同')
    routed=[]
    for row in evaluation:
        key=row['id'];prior=by[key]
        if prior['source_sha256']!=row['source_sha256'] or prior['expected']!=row['action_id']:raise ValueError('原父候选来源/标签不同')
        if route_needed(prior['candidate']):
            semantic=project_path(observed[key]['semantic_cache']) if key in observed else project_path(row['cache'])
            if sha(semantic)!=expected[key]['semantic_cache_sha256']:raise ValueError('原实际取帧缓存不同')
            with np.load(semantic,allow_pickle=False) as a:row['actual_ms']=a['timestamps'].astype(np.int64).tolist()
            routed.append(row)
    assert_source_split(training,evaluation);enforce_source_policy(training+routed,operation='inference')
    return training,evaluation,routed,parent,expected,observed


def pixels(row,config,verified):
    if row['path'] not in verified:verified[row['path']]=sha(project_path(row['path']))
    if verified[row['path']]!=row['source_sha256']:raise ValueError('视频编码器适配原片改变')
    images,_=recovering_images(row,row['actual_ms']);return fit_pixels(images,config).astype(np.float16)


def teachers(entries,adviser,*,verify=False):
    path=OUT/'domain-adaptation-features.npz'
    with np.load(path,allow_pickle=False) as a:known_ids={r['id'] for r in json.loads(str(a['header']))['records']}
    known=[r for r in entries if r['id'] in known_ids];_,known_prob,_,_,labels,known_info=load_features(known)
    if known_info['new_encoder_calls']!=0:raise ValueError('已有全帧教师输入不应重新编码')
    output={r['id']:p for r,p in zip(known,known_prob)};cache_path=OUT/'domain-encoder-extra-teachers.json'
    policy={'checkpoint_sha256':adviser.manifest['sha256'],'labels':labels,'extract_code_sha256':hashlib.sha256(inspect.getsource(type(adviser).extract_head_features).encode()).hexdigest(),
            'fit_pixels_sha256':hashlib.sha256(inspect.getsource(fit_pixels).encode()).hexdigest(),'known_features_sha256':known_info['sha256'],'feature_policy_sha256':known_info['policy_sha256']}
    signature=fingerprint(policy);cache={'schema_version':1,'policy':policy,'signature':signature,'rows':[]}
    if cache_path.exists():
        cache=read(cache_path)
        if cache['signature']!=signature or fingerprint(cache['policy'])!=signature:raise ValueError('额外教师缓存策略不同')
    records={r['id']:r for r in cache['rows']};hits=new=0;verified={}
    if len(records)!=len(cache['rows']):raise ValueError('额外教师缓存输入重复')
    for row in entries:
        if row['id'] in output:continue
        identity={k:row[k] for k in ('id','source_sha256','group','start_ms','end_ms','crop','actual_ms')};key=row['id']
        if key in records:
            record=records[key]
            if record['identity']!=identity:raise ValueError('额外教师实际输入不同')
            probability=np.asarray(record['probability'],np.float32);hits+=1
        else:
            if verify:raise ValueError('核查模式缺少教师缓存，不自动调用GPU')
            if row['path'] not in verified:verified[row['path']]=sha(project_path(row['path']))
            if verified[row['path']]!=row['source_sha256']:raise ValueError('原片改变')
            frames,_=recovering_images(row,row['actual_ms']);_,probability=adviser.extract_head_features(frames);new+=1
            record={'id':key,'identity':identity,'probability':probability.tolist(),'probability_sha256':hashlib.sha256(probability.tobytes()).hexdigest()};records[key]=record
            cache['rows']=list(records.values());atomic_json(cache_path,cache)
        if probability.shape!=(len(labels),) or not np.isfinite(probability).all() or np.any(probability<0) or not np.isclose(probability.sum(),1,atol=1e-5) or hashlib.sha256(probability.tobytes()).hexdigest()!=record['probability_sha256']:raise ValueError('额外教师分布损坏')
        output[key]=probability
    return np.asarray([output[r['id']] for r in entries],np.float32),{'known_cache_hits':len(known),'extra_cache_hits':hits,'new_teacher_calls':new,'path':cache_path.relative_to(ROOT).as_posix(),'sha256':sha(cache_path),'policy_sha256':signature}


def controls(entries,adviser,*,verify=False):
    """对照只需top/分数/间隔；原完整分布或已校验旧验证输出足够时不编码。"""
    with np.load(OUT/'domain-adaptation-features.npz',allow_pickle=False) as a:known_ids={r['id'] for r in json.loads(str(a['header']))['records']}
    known=[r for r in entries if r['id'] in known_ids];_,probabilities,_,_,labels,info=load_features(known)
    if info['new_encoder_calls']:raise ValueError('原对照输入不应重新编码')
    output={}
    for row,probability in zip(known,probabilities):
        order=np.argsort(probability)[::-1];score=float(probability[order[0]])
        output[row['id']]={'top':labels[order[0]],'score':score,'margin':score-float(probability[order[1]])}
    path=OUT/'domain-encoder-unadapted-controls.json';policy={'checkpoint_sha256':adviser.manifest['sha256'],'known_cache_sha256':info['sha256'],
        'old_training_report_sha256':sha(ROOT/'data/verification/step-22/xclip-fitness-training.json'),'sampling_sha256':sha(ROOT/'fitness_app/video_sampling.py')}
    signature=fingerprint(policy);cache={'schema_version':1,'policy':policy,'signature':signature,'rows':[]}
    if path.exists():
        cache=read(path)
        if cache['signature']!=signature or fingerprint(cache['policy'])!=signature:raise ValueError('原头对照缓存策略不同')
    records={r['id']:r for r in cache['rows']};old_inputs={r['id']:r for r in adviser.report['identity']['inputs']};old_predictions={r['id']:r for r in adviser.report['validation']['predictions']};hits=reused=new=0
    if len(records)!=len(cache['rows']):raise ValueError('原头对照缓存重复')
    for row in entries:
        key=row['id']
        if key in output:continue
        identity={k:row[k] for k in ('id','source_sha256','group','start_ms','end_ms','crop','actual_ms')}
        if key in records:
            record=records[key]
            if record['identity']!=identity:raise ValueError('原头对照输入不同')
            prediction=record['prediction'];hits+=1
        else:
            if verify:raise ValueError('核查模式缺少原头对照缓存')
            if sha(project_path(row['path']))!=row['source_sha256']:raise ValueError('原片变化')
            try:images,actual=sample_frames(row)
            except ValueError:images,_=recovering_images(row,row['actual_ms']);actual=None
            prior=old_predictions.get(key)
            if actual is not None and reusable_prediction(row,old_inputs.get(key),prior,actual,row['actual_ms']):
                prediction={k:prior[k] for k in ('top','score','margin')};origin='same_checkpoint_exact_frames_original_validation';reused+=1
            else:
                if actual!=row['actual_ms']:images,_=recovering_images(row,row['actual_ms'])
                prediction=adviser.predict(images);origin='fresh_missing_control';new+=1
            record={'id':key,'identity':identity,'prediction':prediction,'origin':origin};records[key]=record;cache['rows']=list(records.values());atomic_json(path,cache)
        if prediction['top'] not in labels or not all(type(prediction[k]) in (int,float) and np.isfinite(prediction[k]) and 0<=prediction[k]<=1 for k in ('score','margin')):raise ValueError('原头对照输出无效')
        output[key]=prediction
    return output,{'known_cache_hits':len(known),'extra_cache_hits':hits,'legacy_reused':reused,'new_control_calls':new,'path':path.relative_to(ROOT).as_posix(),'sha256':sha(path)}
