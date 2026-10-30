"""硬拉视觉单因素对照：只增加目标前景掩膜，不改人物划分或验收门槛。"""
import hashlib
import importlib.metadata
import inspect
import json
import time
from collections import Counter

import cv2
import numpy as np
from PIL import Image,ImageDraw,ImageFont

from . import deadlift_visual as base
from .paths import ROOT
from .pose import DEFAULT_MODEL,model_status
from .research_data import load_dataset
from .research_extract import atomic_json

MASK_CONFIG={'mode':'IMAGE','num_poses':2,'confidence':.5,'maximum_torso_error':.25,'minimum_candidate_margin':.25,
             'mask_threshold':.5,'minimum_foreground_fraction':.01,'maximum_foreground_fraction':.95,
             'all_eight_masks_required':True,'background_rgb':[127,127,127]}
CACHE=ROOT/'data/experiments/features/deadlift-visual-masked.npz'
REPORT=ROOT/'data/experiments/deadlift-visual-masked.json'
PREVIEW=ROOT/'data/verification/step-17/deadlift-masked-crops.png'


class MaskUnassessable(ValueError):pass


def matched_target(poses,expected,width,height):
    length=float(np.linalg.norm(expected[1]-expected[0]));scores=[]
    if length<=0:raise MaskUnassessable('INVALID_TORSO_REFERENCE')
    for index,p in enumerate(poses):
        p=np.asarray(p,float)
        if p.shape!=(33,5) or not np.isfinite(p[[11,12,23,24]]).all() or (p[[11,12,23,24],3:]<.5).any():continue
        xy=p[[11,12,23,24],:2]*[width,height]
        observed=np.array([xy[:2].mean(axis=0),xy[2:].mean(axis=0)])
        scores.append((float(np.linalg.norm(observed-expected,axis=1).mean()/length),index))
    scores.sort()
    if not scores or scores[0][0]>MASK_CONFIG['maximum_torso_error']:raise MaskUnassessable('MASK_TARGET_NOT_MATCHED')
    if len(scores)>1 and scores[1][0]-scores[0][0]<MASK_CONFIG['minimum_candidate_margin']:
        raise MaskUnassessable('MASK_TARGET_AMBIGUOUS')
    return scores[0][1]


def foreground_image(rgb,mask,expected):
    mask=np.asarray(mask)
    if mask.shape!=rgb.shape[:2] or not np.isfinite(mask).all() or (mask<0).any() or (mask>1).any():
        raise MaskUnassessable('INVALID_MASK')
    foreground=mask>=MASK_CONFIG['mask_threshold'];fraction=float(foreground.mean())
    if not MASK_CONFIG['minimum_foreground_fraction']<=fraction<=MASK_CONFIG['maximum_foreground_fraction']:
        raise MaskUnassessable('MASK_AREA_UNCONFIRMED')
    for x,y in expected:
        col=int(round(x));row=int(round(y))
        if not (0<=row<mask.shape[0] and 0<=col<mask.shape[1]) or not foreground[row,col]:
            raise MaskUnassessable('TORSO_OUTSIDE_MASK')
    return np.where(foreground[:,:,None],rgb,127).astype(np.uint8)


class MaskedBackbone(base.VisualBackbone):
    def __init__(self):
        super().__init__()
        if not model_status()['available']:raise ValueError('Full掩膜模型身份不符')
        import mediapipe as mp
        from mediapipe.tasks import python
        from mediapipe.tasks.python import vision
        self.mp=mp;self.calls=0;self.preview_hook=None
        options=vision.PoseLandmarkerOptions(base_options=python.BaseOptions(model_asset_path=str(DEFAULT_MODEL),delegate=python.BaseOptions.Delegate.CPU),
            running_mode=vision.RunningMode.IMAGE,num_poses=2,output_segmentation_masks=True,
            min_pose_detection_confidence=.5,min_pose_presence_confidence=.5,min_tracking_confidence=.5)
        self.segmenter=vision.PoseLandmarker.create_from_options(options)

    def embed(self,rgb,matrix):
        inverse=cv2.invertAffineTransform(matrix)
        expected=np.array([inverse@np.array([112,56,1]),inverse@np.array([112,168,1])])
        self.calls+=1;result=self.segmenter.detect(self.mp.Image(image_format=self.mp.ImageFormat.SRGB,data=np.ascontiguousarray(rgb)))
        poses=[[[p.x,p.y,p.z,p.visibility or 0,p.presence or 0] for p in pose] for pose in result.pose_landmarks]
        masks=result.segmentation_masks or []
        if poses and len(masks)!=len(poses):raise RuntimeError('请求了掩膜，但模型没有返回对应实例，停止而非伪造拒判')
        try:
            index=matched_target(poses,expected,rgb.shape[1],rgb.shape[0])
            masked=foreground_image(rgb,np.squeeze(masks[index].numpy_view()),expected)
        except MaskUnassessable as error:
            if self.preview_hook:self.preview_hook(rgb,None,matrix,str(error))
            raise
        if self.preview_hook:self.preview_hook(rgb,masked,matrix,None)
        return super().embed(masked,matrix)

    def close(self):self.segmenter.close()


def feature_signature():
    record={'base':base.feature_signature(),'config':MASK_CONFIG,'pose_model_sha256':base.digest(DEFAULT_MODEL),
            'mediapipe':importlib.metadata.version('mediapipe'),'target_matching':inspect.getsource(matched_target),
            'mask_processing':inspect.getsource(foreground_image),'backbone':inspect.getsource(MaskedBackbone)}
    return hashlib.sha256(json.dumps(record,sort_keys=True).encode()).hexdigest()


def render_preview(frames,cases,signature):
    canvas=Image.new('RGB',(1160,1250),'#f3f5f4');draw=ImageDraw.Draw(canvas)
    font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',18);small=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',14)
    draw.text((15,12),'单因素对照 · 原躯干灰度裁片／目标前景裁片（拟合前核查）',font=font,fill='#254a3b')
    for group,case in enumerate(cases):
        top=48+group*575;draw.text((15,top),'来源'+case['source_label']+' · 整段标签，单帧未标错',font=font,fill='#254a3b')
        selected=[f for f in frames if f['sample_key']==case['sample_key']]
        for col,record in enumerate(selected[:4]):
            x=15+col*285
            raw=cv2.warpAffine(record['rgb'],record['matrix'],(224,224),borderValue=(127,127,127))
            canvas.paste(Image.fromarray(cv2.cvtColor(raw,cv2.COLOR_RGB2GRAY)).convert('RGB'),(x,top+35))
            draw.text((x,top+263),f"{record['source_ms']/1000:.3f}s · 原裁片",font=small,fill='#405b50')
            if record['masked'] is not None:
                image=cv2.warpAffine(record['masked'],record['matrix'],(224,224),borderValue=(127,127,127))
                canvas.paste(Image.fromarray(cv2.cvtColor(image,cv2.COLOR_RGB2GRAY)).convert('RGB'),(x,top+300))
            else:draw.text((x,top+350),'拒判：'+record['error'],font=small,fill='#9a422f')
            draw.text((x,top+530),'目标掩膜／不添加错误标签',font=small,fill='#405b50')
    draw.text((15,1220),'MyDeadlift / Ivan Conanta, Gloria Virginia · CC BY 4.0 · 工程复核，不是独立逐帧真值',font=small,fill='#405b50')
    canvas.save(PREVIEW)
    return {'path':PREVIEW.relative_to(ROOT).as_posix(),'sha256':base.digest(PREVIEW),'feature_signature':signature,
            'inspected':False,'cases':cases,'frames':len(frames),'scope':'仅检查分割和裁片，不建立新错误真值'}


def development(features_only=False,reuse_only=False):
    original=json.loads(base.REPORT.read_text(encoding='utf-8'))
    if original['status']!='computed' or original['feature_signature']!=base.feature_signature() or original['test_used']:
        raise ValueError('原对照结果或特征协议不一致，不混合比较')
    samples=[s for s in load_dataset() if s.action_id=='conventional_deadlift' and s.partition=='development'];base.require_development(samples)
    if len(samples)!=97:raise ValueError('固定开发划分数量变化')
    baseline=json.loads((ROOT/'data/experiments/baseline.json').read_text(encoding='utf-8'))
    identities={s.key:base.input_identity(s,baseline) for s in samples};feature_id=feature_signature()
    signature=hashlib.sha256(json.dumps({'config':MASK_CONFIG,'features':feature_id,'inputs':{k:v[1] for k,v in identities.items()},
        'implementation':base.digest(__file__),'split_sha256':base.digest(ROOT/'data/experiments/split.json'),
        'fit_code':inspect.getsource(base.fit_fold),'prediction_code':inspect.getsource(base.predictions)},sort_keys=True).encode()).hexdigest()
    previous=json.loads(REPORT.read_text(encoding='utf-8')) if REPORT.exists() else {};review=previous.get('visual_review')
    if review and review['feature_signature']!=feature_id:review=None
    if previous.get('signature')==signature and (previous.get('status')=='computed' or features_only and previous.get('status')=='features_ready'):
        print(json.dumps({'cache_hit':True,'test_used':False,'status':previous['status'],'metrics':previous.get('metrics',{}).get('main')}),flush=True);return previous
    if reuse_only:raise ValueError('没有对应掩膜候选复用身份，未推理／拟合')
    if not features_only and (not review or not review.get('inspected') or base.digest(ROOT/review['path'])!=review['sha256']):
        raise ValueError('先核查实际掩膜裁片，再拟合')
    features=base.read_cache(feature_id,CACHE);backbone=None;new=0;hits=0;previews=[];cases=original['visual_review']['cases']
    qc={row['sample_key']:row for row in cases};calls=0
    try:
        for sample in samples:
            pose,fingerprint=identities[sample.key];existing=features.get(sample.key)
            if existing and existing['fingerprint']==fingerprint:hits+=1;continue
            if backbone is None:backbone=MaskedBackbone()
            state={'index':0};reference=original['feature_summaries'][sample.key]
            def preview_hook(rgb,masked,matrix,error):
                i=state['index'];state['index']+=1
                if i in (0,2,5,7):previews.append({'sample_key':sample.key,'source_ms':reference['sampled_source_ms'][i],
                    'rgb':rgb.copy(),'masked':masked.copy() if masked is not None else None,'matrix':matrix.copy(),'error':error})
            backbone.preview_hook=preview_hook if sample.key in qc else None
            before=backbone.calls
            try:vector,reason,summary=base.extract_sample(sample,pose,backbone)
            except MaskUnassessable as error:
                vector=None;reason=str(error);summary={'input_geometry':reference,'mask_error':str(error)}
            added=backbone.calls-before;calls+=added;summary['new_segmentation_task_calls']=added
            features[sample.key]={'sample_key':sample.key,'fingerprint':fingerprint,'features':vector,'reason':reason,'summary':summary};new+=1
            if new%10==0:base.save_cache(feature_id,features,CACHE);print(json.dumps({'phase':'masked_features','new_clips':new,'task_calls':calls,'cache_hits':hits}),flush=True)
        base.save_cache(feature_id,features,CACHE)
    finally:
        if backbone is not None:backbone.close()
    if features_only:
        if previews:review=render_preview(previews,cases,feature_id)
        result={'status':'features_ready','signature':signature,'feature_signature':feature_id,'test_used':False,'config':MASK_CONFIG,
                'new_feature_clips':new,'feature_cache_hits':hits,'new_segmentation_task_calls':calls,
                'visual_review':review,'feature_summaries':{s.key:features[s.key]['summary'] for s in samples},
                'rejection_reasons':dict(Counter(features[s.key]['reason'] for s in samples if features[s.key]['reason']))}
        atomic_json(REPORT,result);print(json.dumps({'phase':'masked_features_ready','new_clips':new,'task_calls':calls,'cache_hits':hits,'test_used':False}),flush=True);return result
    rows=[];folds=[]
    for fold in range(4):
        train=[s for s in samples if s.validation_fold!=fold];validation=[s for s in samples if s.validation_fold==fold]
        try:model,record=base.fit_fold(train,validation,features);error=None
        except base.InsufficientVisualFeatures as caught:model=None;error=str(caught);record={'actual_fit_keys':[],'error':error}
        folds.append({**record,'fold':fold});rows.extend(base.predictions(model,validation,features,error))
    result={'status':'computed','signature':signature,'feature_signature':feature_id,'test_used':False,'config':MASK_CONFIG,
            'new_feature_clips':new,'feature_cache_hits':hits,'new_segmentation_task_calls':calls,'folds':folds,'predictions':rows,
            'metrics':base.evaluate(rows),'visual_review':review,'feature_summaries':{s.key:features[s.key]['summary'] for s in samples},
            'unmasked_reference':{'signature':original['signature'],'metrics':original['metrics']['main']},'deployment':'not_deployed',
            'limitation':'仅整段GB/PB，实例关联只服务内部特征，不改变产品多人拒判，不产生每帧／每次／局部脊柱真值'}
    atomic_json(REPORT,result);print(json.dumps({'phase':'masked_development','test_used':False,'metrics':result['metrics']['main'],
        'acceptance_passed':result['metrics']['acceptance_passed']}),flush=True);return result
