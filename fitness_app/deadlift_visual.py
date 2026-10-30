"""整段GB/PB视觉候选：不作为帧标签、计数真值或已发布纠错方法。"""
import hashlib
import inspect
import io
import json
import os
import time

import av
import cv2
import numpy as np
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVC

from .paths import ROOT, project_path
from .media import display_rgb, source_stamp
from .research_data import SEED, load_dataset
from .research_metrics import evaluate
from .research_extract import atomic_json

CONFIG={'schema':'deadlift-torso-visual-1','frames':8,'minimum_coverage':.7,'confidence':.5,
        'minimum_torso_pixels':24,'minimum_crop_fraction':.9,'crop_size':224,
        'shoulder_target':[112,56],'hip_target':[112,168],
        'channels':'grayscale_repeated_3','pool_tensor':'464','pool_dimensions':1280,
        'horizontal_flip':'average_embeddings','aggregation':['mean','std_ddof_0'],
        'classifier':{'kernel':'linear','C':1.,'class_weight':'balanced','seed':SEED},
        'truth_scope':'source_clip_GB_PB_only','test_images_allowed':False}
CACHE=ROOT/'data/experiments/features/deadlift-visual.npz'
REPORT=ROOT/'data/experiments/deadlift-visual.json'


class InsufficientVisualFeatures(ValueError):pass


def digest(path):
    with project_path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def torso_geometry(points,width,height,person_count=1,identity_accepted=True):
    """肩髋对齐仅供图像特征裁片，不能解释为脊柱角度。"""
    if person_count!=1 or not identity_accepted:return None
    if not np.isfinite([width,height]).all() or min(width,height)<=0:return None
    p=np.asarray(points,dtype=float)
    if p.shape!=(33,5) or not np.isfinite(p[[11,12,23,24]]).all():return None
    selected=p[[11,12,23,24]]
    if (selected[:,3:]<CONFIG['confidence']).any() or (selected[:,:2]<0).any() or (selected[:,:2]>1).any():return None
    xy=selected[:,:2]*[width,height];shoulder=xy[:2].mean(axis=0);hip=xy[2:].mean(axis=0)
    length=float(np.linalg.norm(hip-shoulder))
    if length<CONFIG['minimum_torso_pixels']:return None
    down=(hip-shoulder)/length;right=np.array([down[1],-down[0]]);scale=112/length
    matrix=np.zeros((2,3),dtype=np.float32);matrix[0,:2]=right*scale;matrix[1,:2]=down*scale
    matrix[:,2]=np.array(CONFIG['shoulder_target'])-matrix[:,:2]@shoulder
    corners=np.array([[0,0],[width,0],[width,height],[0,height]],np.float32)
    transformed=corners@matrix[:,:2].T+matrix[:,2]
    square=np.array([[0,0],[224,0],[224,224],[0,224]],np.float32)
    area,_=cv2.intersectConvexConvex(transformed,square)
    if area/(224*224)<CONFIG['minimum_crop_fraction']:return None
    return matrix


class VisualBackbone:
    def __init__(self):
        meta=json.loads((ROOT/'data/models/mobilenetv2.json').read_text(encoding='utf-8'))
        path=ROOT/'data/models/mobilenetv2.onnx'
        if digest(path)!=meta['sha256']:raise ValueError('视觉骨干身份不符，未加载')
        cv2.setNumThreads(1)
        self.net=cv2.dnn.readNetFromONNX(str(path))
        self.net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
        self.net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
        self.mean=np.array([.485,.456,.406],np.float32);self.std=np.array([.229,.224,.225],np.float32)

    def embed(self,rgb,matrix):
        patch=cv2.warpAffine(rgb,matrix,(224,224),flags=cv2.INTER_LINEAR,borderMode=cv2.BORDER_CONSTANT,borderValue=(127,127,127))
        gray=cv2.cvtColor(patch,cv2.COLOR_RGB2GRAY);rgb_gray=np.repeat(gray[:,:,None],3,axis=2)
        outputs=[]
        for image in (rgb_gray,rgb_gray[:,::-1]):
            blob=((image.astype(np.float32)/255-self.mean)/self.std).transpose(2,0,1)[None]
            self.net.setInput(np.ascontiguousarray(blob));values=self.net.forward(CONFIG['pool_tensor']).reshape(-1)
            if values.shape!=(1280,) or not np.isfinite(values).all():raise ValueError('视觉池化输出维数或有限性不符')
            outputs.append(values.copy())
        return np.mean(outputs,axis=0)


def feature_signature():
    meta=json.loads((ROOT/'data/models/mobilenetv2.json').read_text(encoding='utf-8'))
    payload={'config':{k:v for k,v in CONFIG.items() if k not in ('classifier','test_images_allowed')},
             'backbone':meta['sha256'],'geometry':inspect.getsource(torso_geometry),'embedding':inspect.getsource(VisualBackbone),
             'extractor':inspect.getsource(extract_sample),'opencv':cv2.__version__,'numpy':np.__version__,'av':av.__version__}
    return hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()


def read_cache(signature,path=None):
    path=project_path(path or CACHE)
    if not path.exists():return {}
    with np.load(path,allow_pickle=False) as archive:
        meta=json.loads(str(archive['metadata']))
        if meta['signature']!=signature:return {}
        vectors=archive['vectors']
        if vectors.shape!=(len(meta['records']),2560):raise ValueError('视觉缓存行数／维数不符')
        result={}
        for row,vector in zip(meta['records'],vectors):
            valid=row['reason'] is None
            if valid and not np.isfinite(vector).all():raise ValueError('视觉缓存不是有限向量')
            result[row['sample_key']]={**row,'features':vector.copy() if valid else None}
        if len(result)!=len(meta['records']):raise ValueError('视觉缓存样本重复')
        return result


def save_cache(signature,rows,path=None):
    keys=sorted(rows);records=[{k:v for k,v in rows[key].items() if k!='features'} for key in keys]
    vectors=np.array([rows[key]['features'] if rows[key]['features'] is not None else np.full(2560,np.nan) for key in keys],np.float32)
    buffer=io.BytesIO();np.savez_compressed(buffer,metadata=json.dumps({'signature':signature,'records':records}),vectors=vectors)
    target=project_path(path or CACHE);temporary=target.with_suffix('.npz.tmp');owned=False
    try:
        with temporary.open('xb') as f:
            owned=True;f.write(buffer.getvalue());f.flush();os.fsync(f.fileno())
        os.replace(temporary,target);owned=False
    finally:
        if owned:temporary.unlink()


def input_identity(sample,baseline):
    entry=baseline['extractions'][sample.key]
    feature_path=ROOT/'data/experiments/features'/f"{entry['feature_key']}.json"
    feature=json.loads(feature_path.read_text(encoding='utf-8'));pose_path=project_path(feature['pose_cache'])
    pose_path.relative_to(ROOT/'data/experiments/poses')
    stat=sample.absolute_path.stat();source={'source_sha256':sample.sha256,'bytes':stat.st_size,'modified_ns':stat.st_mtime_ns}
    with np.load(pose_path,allow_pickle=False) as archive:meta=json.loads(str(archive['metadata']))
    if meta['pose_key']!=entry['pose_key'] or meta['source']!=source:raise ValueError('视觉输入的原片／姿态缓存身份不符')
    fingerprint=hashlib.sha256(json.dumps({'source':source,'pose_sha256':digest(pose_path)},sort_keys=True).encode()).hexdigest()
    return pose_path,fingerprint


def extract_sample(sample,pose_path,backbone):
    if sample.partition!='development':raise ValueError('当前视觉开发协议禁止读取独立测试或排除片段')
    if digest(sample.absolute_path)!=sample.sha256:raise ValueError('视觉原片不符合来源摘要')
    with np.load(pose_path,allow_pickle=False) as archive:
        stamps=archive['timestamps_ms'].copy();points=archive['points'].copy()
        counts=archive['person_counts'].copy();accepted=archive['identity_accepted'].copy()
    if not (len(stamps)==len(points)==len(counts)==len(accepted)):raise ValueError('姿态缓存的逐帧长度不同')
    with av.open(str(sample.absolute_path)) as container:
        stream=container.streams.video[0];origin=round(float(stream.start_time*stream.time_base)*1000) if stream.start_time is not None else 0
        decoded=container.decode(video=0);first=next(decoded);rgb=display_rgb(first);height,width=rgb.shape[:2]
        geometries={}
        for i in range(len(stamps)):
            matrix=torso_geometry(points[i],width,height,int(counts[i]),bool(accepted[i]))
            if matrix is not None:geometries[i]=matrix
        coverage=len(geometries)/len(stamps) if len(stamps) else 0
        summary={'decoded_frames':len(stamps),'valid_torso_frames':len(geometries),'coverage':coverage}
        if len(geometries)<8 or coverage<CONFIG['minimum_coverage']:
            return None,'INSUFFICIENT_TORSO_COVERAGE',summary
        indices=list(geometries);chosen=[indices[i] for i in np.rint(np.linspace(0,len(indices)-1,8)).astype(int)]
        import itertools
        embeddings=[];seen=0;selected=[];began=time.perf_counter()
        for i,frame in enumerate(itertools.chain((first,),decoded)):
            if i>=len(stamps) or source_stamp(frame,origin)!=int(stamps[i]):raise ValueError('视觉源帧时间与姿态缓存不一致')
            seen+=1
            if i in chosen:
                current=display_rgb(frame)
                if current.shape[:2]!=(height,width):raise ValueError('视觉源帧中途改尺寸')
                embeddings.append(backbone.embed(current,geometries[i]));selected.append(int(stamps[i]))
        if seen!=len(stamps) or len(embeddings)!=8:raise ValueError('视觉原片未完整对齐8个采样')
    values=np.asarray(embeddings,np.float32)
    return np.concatenate((values.mean(axis=0),values.std(axis=0))),None,{**summary,'sampled_source_ms':selected,'decode_embedding_ms':(time.perf_counter()-began)*1000}


def require_development(samples):
    if any(s.action_id!='conventional_deadlift' or s.partition!='development' for s in samples):
        raise ValueError('视觉开发仅允许硬拉development，禁止独立test拟合／提取')


def fit_fold(training,validation,features):
    require_development(training+validation)
    if {s.key for s in training}&{s.key for s in validation} or {s.subject_group for s in training}&{s.subject_group for s in validation}:
        raise ValueError('视觉开发的训练／验证人物或样本交叉')
    usable=[s for s in training if features[s.key]['features'] is not None]
    x=np.array([features[s.key]['features'] for s in usable],float);y=np.array([int(s.label=='PB') for s in usable])
    if len(set(y))!=2:raise InsufficientVisualFeatures('视觉训练没有两类可用特征')
    if not np.isfinite(x).all():raise ValueError('视觉训练含非有限特征')
    model=make_pipeline(StandardScaler(),SVC(kernel='linear',C=1.,class_weight='balanced',random_state=SEED))
    model.fit(x,y)
    return model,{'requested_fit_keys':[s.key for s in training],'actual_fit_keys':[s.key for s in usable],
                  'fit_subjects':sorted({s.subject_group for s in usable}),'evaluation_keys':[s.key for s in validation],
                  'scaler_sha256':hashlib.sha256(model.named_steps['standardscaler'].mean_.tobytes()).hexdigest()}


def predictions(model,samples,features,fit_error=None):
    rows=[]
    for sample in samples:
        item=features[sample.key];values=item['features'];prediction=None
        if model is not None and values is not None:prediction=int(model.predict(np.asarray([values],float))[0])
        rows.append({'sample_key':sample.key,'truth':int(sample.label=='PB'),'prediction':prediction,
                     'reason':(fit_error or item['reason']) if prediction is None else None,'subject_group':sample.subject_group,'view':'|'.join(sample.views)})
    return rows


def development(features_only=False,reuse_only=False):
    samples=[s for s in load_dataset() if s.action_id=='conventional_deadlift' and s.partition=='development'];require_development(samples)
    baseline=json.loads((ROOT/'data/experiments/baseline.json').read_text(encoding='utf-8'))
    if len(samples)!=97 or len({s.key for s in samples})!=97:raise ValueError('固定硬拉开发划分数量变化')
    identities={s.key:input_identity(s,baseline) for s in samples}
    feature_id=feature_signature();signature=hashlib.sha256(json.dumps({'config':CONFIG,'feature_signature':feature_id,
        'inputs':{key:value[1] for key,value in identities.items()},'implementation':digest(__file__),
        'split_sha256':digest(ROOT/'data/experiments/split.json')},sort_keys=True).encode()).hexdigest()
    previous=json.loads(REPORT.read_text(encoding='utf-8')) if REPORT.exists() else {}
    review=previous.get('visual_review')
    if review and review.get('feature_signature')!=feature_id:review=None
    if previous:
        if (previous['signature']==signature or signature in previous.get('equivalent_signatures',[])) and (previous.get('status')=='computed' or features_only and previous.get('status')=='features_ready'):
            print(json.dumps({'cache_hit':True,'test_used':False,'status':previous['status'],'metrics':previous.get('metrics',{}).get('main')}),flush=True);return previous
    if reuse_only:raise ValueError('没有对应的视觉结果复用身份；未启动新推理或拟合')
    if not features_only and (not review or not review.get('inspected') or digest(ROOT/review['path'])!=review['sha256']):
        raise ValueError('先核对当前特征协议的实际裁片，再拟合视觉候选；不要求新的用户确认')
    atomic_json(REPORT,{'status':'planned','signature':signature,'config':CONFIG,'test_used':False,'development_samples':len(samples)})
    features=read_cache(feature_id);backbone=None;new=0;hits=0
    for sample in samples:
        pose,fingerprint=identities[sample.key];existing=features.get(sample.key)
        if existing and existing['fingerprint']==fingerprint:hits+=1;continue
        if backbone is None:backbone=VisualBackbone()
        vector,reason,summary=extract_sample(sample,pose,backbone)
        features[sample.key]={'sample_key':sample.key,'fingerprint':fingerprint,'reason':reason,'features':vector,'summary':summary};new+=1
        if new%10==0:save_cache(feature_id,features);print(json.dumps({'phase':'visual_features','new_clips':new,'cache_hits':hits}),flush=True)
    save_cache(feature_id,features)
    if features_only:
        result={'status':'features_ready','signature':signature,'feature_signature':feature_id,'config':CONFIG,'test_used':False,
                'new_visual_feature_clips':new,'feature_cache_hits':hits,'new_pose_inference_frames':0,
                'feature_summaries':{s.key:features[s.key]['summary'] for s in samples},'visual_review':review}
        atomic_json(REPORT,result);print(json.dumps({'phase':'visual_features_ready','new_clips':new,'cache_hits':hits,'test_used':False}),flush=True);return result
    rows=[];folds=[]
    for fold in range(4):
        train=[s for s in samples if s.validation_fold!=fold];validation=[s for s in samples if s.validation_fold==fold]
        try:model,record=fit_fold(train,validation,features);error=None
        except InsufficientVisualFeatures as caught:
            model=None;error=str(caught);record={'requested_fit_keys':[s.key for s in train],'actual_fit_keys':[],'error':error}
        folds.append({**record,'fold':fold});rows.extend(predictions(model,validation,features,error))
    result={'status':'computed','signature':signature,'feature_signature':feature_id,'config':CONFIG,'test_used':False,
            'development_samples':len(samples),'new_visual_feature_clips':new,'feature_cache_hits':hits,'new_pose_inference_frames':0,
            'folds':folds,'predictions':rows,'metrics':evaluate(rows),'feature_summaries':{s.key:features[s.key]['summary'] for s in samples},'visual_review':review,
            'deployment':'not_deployed','limitation':'仅整段来源GB/PB；未形成每帧／每次／局部脊柱真值，不作发力或肌肉判断'}
    atomic_json(REPORT,result)
    print(json.dumps({'cache_hit':False,'test_used':False,'metrics':result['metrics']['main'],'acceptance_passed':result['metrics']['acceptance_passed']}),flush=True)
    return result
