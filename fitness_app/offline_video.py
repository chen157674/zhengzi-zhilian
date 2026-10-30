"""同步离线视频窗口与有界数值姿态缓存；RGB仅在滚动内存中，不重复姿态推理。"""
from collections import deque
from dataclasses import dataclass
import time
import numpy as np
from .auth import APIError
from .automatic_recognition import body_frame
from .frame_pipeline import analysis_framing
from .content_bounds import detect_content_bounds
from .gym_pose import GymPoseService
from .media import video_frames,MAX_DURATION_MS
from .sessions import IdentityContinuity
from .semantic_recognition import shared_predictor,point_array

POSE_SAMPLE_MS=40
IMAGE_SAMPLE_MS=350
WINDOW_SPAN_MS=3200
WINDOW_STRIDE_MS=1600
MAX_ROWS=45002


@dataclass(slots=True)
class NumericFrame:
    stamp:int
    width:int
    height:int
    points:np.ndarray
    world:np.ndarray
    people:int
    bounds:tuple
    model:str
    identity_accepted:bool
    scene_reference:dict|None=None
    pretrained:dict|None=None

    def pose_result(self):
        keys=('x','y','z','visibility','presence')
        points=[{k:float(v) for k,v in zip(keys,row)} for row in self.points]
        world=[{k:float(v) for k,v in zip(keys,row)} for row in self.world]
        return {'poses':[points] if self.people==1 else [[]]*min(self.people,2),
                'world_poses':[world] if self.people==1 and np.isfinite(self.world[:,:3]).any() else [],
                'content_bounds':list(self.bounds),'model':self.model,'scene_reference':self.scene_reference}

    def frame(self,action,index):
        result=self.pose_result()
        frame={'captured_ms':self.stamp,'frame_seq':index,'width':self.width,'height':self.height,
                'framing':analysis_framing(action,result),'model_version':self.model,
                'identity_accepted':self.identity_accepted}
        if self.pretrained is not None:frame['pretrained']=self.pretrained
        return frame


@dataclass
class OfflineScan:
    rows:list
    decisions:list
    unassessable:list
    end_ms:int
    model_version:str|None
    pose_calls:int

    def clear(self):self.rows.clear();self.decisions.clear();self.unassessable.clear()


def scan_video(path,info,*,automatic=True,start_ms=0,end_ms=None,pose_factory=GymPoseService,
               predictor_provider=shared_predictor,frames=video_frames,check=lambda:None,cancelled=lambda:False,progress=lambda stamp,model:None):
    rows=[];windows=[];gaps=[];images=deque(maxlen=12);observed=deque(maxlen=110)
    identity=IdentityContinuity();model=None;iterator=None;predictor=None;model_version=None
    previous=None;sample_bin=-1;last_image=-10000;last_window=-10000;gap_start=None;gap_reason=None
    first=None;last=None;last_progress=-10000;last_check=0.;pose_calls=0;last_size=None

    def stopped():
        if cancelled():raise APIError('ANALYSIS_CANCELLED','视频处理已取消',409)

    try:
        check();stopped();model=pose_factory(video=True);iterator=frames(path,info,start_ms=start_ms,end_ms=end_ms)
        for stamp,rgb in iterator:
            stopped()
            if time.monotonic()-last_check>=.25:check();last_check=time.monotonic()
            if stamp//POSE_SAMPLE_MS==sample_bin:continue
            sample_bin=stamp//POSE_SAMPLE_MS
            if not 0<=stamp<MAX_DURATION_MS:raise APIError('MEDIA_TIMESTAMPS','视频源时间无效',422)
            if previous is not None and stamp<=previous:raise APIError('MEDIA_TIMESTAMPS','视频源时间未递增',422)
            if first is None:first=stamp
            last=stamp
            output=model.detect(rgb,stamp);pose_calls+=1
            output['content_bounds']=detect_content_bounds(rgb)
            h,w=rgb.shape[:2];generic=body_frame(output,stamp,w,h,len(rows));q=generic['framing'];size=(w,h)
            if previous is not None and stamp-previous>500:
                gaps.append({'start_ms':previous+1,'end_ms':stamp,'reason':'source_gap'});identity.reset();images.clear();observed.clear()
            if last_size is not None and size!=last_size:identity.reset();images.clear();observed.clear()
            good=q['person_count']==1 and identity.accept(generic)
            reason='target_changed' if q['person_count']==1 and not good else 'target_unavailable'
            if not good:
                if gap_start is None:gap_start=stamp;gap_reason=reason
                elif reason=='target_changed':gap_reason=reason
                identity.reset();images.clear();observed.clear()
            elif gap_start is not None:
                if stamp>gap_start:gaps.append({'start_ms':gap_start,'end_ms':stamp,'reason':gap_reason})
                gap_start=gap_reason=None
            points=point_array(q.get('landmarks',[]) if q['person_count']==1 else [])
            worlds=point_array(output['world_poses'][0] if q['person_count']==1 and len(output.get('world_poses',[]))==1 else [])
            row=NumericFrame(stamp,w,h,points,worlds,q['person_count'],tuple(output['content_bounds']),output.get('model','pose-unknown'),good,output.get('scene_reference'),output.get('pretrained'))
            rows.append(row)
            if len(rows)>MAX_ROWS:raise APIError('MEDIA_DURATION','视频采样超过30分钟上限',422)
            if automatic and good:
                observed.append(row)
                if stamp-last_image>=IMAGE_SAMPLE_MS:
                    images.append((stamp,np.ascontiguousarray(rgb).copy()));last_image=stamp
                if len(images)>=8 and stamp-images[0][0]>=2800 and stamp-last_window>=WINDOW_STRIDE_MS:
                    chosen=[v for v in images if stamp-v[0]<=WINDOW_SPAN_MS]
                    if len(chosen)>=8:
                        indices=np.linspace(0,len(chosen)-1,8).round().astype(int);selected=[chosen[i] for i in indices]
                        times=np.asarray([v[0] for v in selected],np.int64)
                        if 1200<=int(times[-1]-times[0])<=4000:
                            check();stopped()
                            if predictor is None:
                                predictor=predictor_provider()
                                threads=getattr(predictor,'info',{}).get('runtime_cpu_threads')
                                if type(threads) is int and 1<=threads<=32:
                                    import torch
                                    torch.set_num_threads(threads)
                            numeric=[r for r in observed if times[0]<=r.stamp<=times[-1]]
                            arrays={'timestamps':np.asarray([r.stamp for r in numeric],np.int64),'points':np.asarray([r.points for r in numeric],np.float32),
                                'world':np.asarray([r.world for r in numeric],np.float32),'sizes':np.asarray([(r.width,r.height) for r in numeric],np.int32),
                                'people':np.ones(len(numeric),np.int32)}
                            decision=predictor.predict([v[1] for v in selected],times,arrays);check();stopped()
                            current_version=decision['model_version']
                            if model_version is not None and current_version!=model_version:raise APIError('MODEL_CHANGED','识别模型中途改变，已停止处理',409)
                            model_version=current_version;last_window=stamp
                            windows.append({'start_ms':int(times[0]),'end_ms':int(times[-1])+1,'action_id':decision['action_id'],
                                'model_version':model_version,'reason':decision.get('reason'),'score':decision.get('score'),
                                'decision_basis':decision.get('decision_basis'),'raw_action_id':decision.get('top_action_id'),
                                'base_model_version':decision.get('base_model_version'),'motion_evidence_applied':decision.get('motion_evidence_applied',False),
                                'motion_evidence':decision.get('motion_evidence')})
            previous=stamp;last_size=size
            if stamp-last_progress>=1000:progress(stamp,model_version);last_progress=stamp
        if last is None:raise APIError('UNDECODABLE_MEDIA','所选范围没有可处理画面',422)
        limit=end_ms if end_ms is not None else info.get('duration_ms') or last+1
        limit=max(last+1,limit)
        if limit>MAX_DURATION_MS:raise APIError('MEDIA_DURATION','视频超过30分钟上限',422)
        if gap_start is not None and limit>gap_start:gaps.append({'start_ms':gap_start,'end_ms':limit,'reason':gap_reason})
        check();stopped();progress(last,model_version)
        return OfflineScan(rows,windows,gaps,limit,model_version,pose_calls)
    except BaseException:
        rows.clear();windows.clear();gaps.clear();raise
    finally:
        images.clear();observed.clear()
        try:
            if iterator is not None and hasattr(iterator,'close'):iterator.close()
        finally:
            if model is not None:model.close()
