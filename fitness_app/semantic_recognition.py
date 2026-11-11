"""有界、异步的连续视频识别；慢推理不占预览锁，换人或关闭后丢弃旧结果。"""
from collections import deque
import logging
import threading
import numpy as np
from .sessions import IdentityContinuity
from .video_vlm import LABELS,CONFIG
from .semantic_model import SemanticPredictor,resources

logger=logging.getLogger(__name__)
_shared_lock=threading.Lock()
_shared_predictor=None


def create_recognizer(fallback=None):
    info,reason=resources()
    if info is not None:return SemanticRecognizer(info=info)
    if reason=='motion_evidence_policy_unavailable':return SemanticRecognizer(disabled_reason=reason)
    if fallback is None:
        from .basic_recognition import BasicRecognizer
        fallback=BasicRecognizer
    return fallback()


def shared_predictor():
    global _shared_predictor
    with _shared_lock:
        if _shared_predictor is None:_shared_predictor=SemanticPredictor()
        return _shared_predictor


def point_array(points):
    result=np.full((33,5),np.nan,np.float32)
    if len(points)!=33:return result
    for i,p in enumerate(points):
        if p.get('valid') is False:continue
        values=[p.get(k,np.nan) for k in ('x','y','z','visibility','presence')]
        if all(isinstance(v,(int,float)) and np.isfinite(v) for v in values):result[i]=values
    return result


class SemanticRecognizer:
    def __init__(self,predictor_provider=shared_predictor,info=None,disabled_reason=None):
        self.info,self.reason=(None,disabled_reason) if disabled_reason else (info,None) if info is not None else resources()
        self.available=self.info is not None;self.provider=predictor_provider
        self.lock=threading.RLock();self.wake=threading.Event();self.thread=None;self.closed=False;self.model_ready=False
        self.identity=IdentityContinuity();self.images=deque(maxlen=24);self.rows=deque(maxlen=100)
        self.generation=0;self.job=None;self.completed=None;self.inference_active=False
        self.last_image=-10000;self.last_queued=-10000;self.last_stamp=None;self.last_seq=None
        self.last_size=None
        self.pending=None;self.pending_since=None;self.confirmed=None;self.latest={}
        self.pending_windows=0;self.pending_window_stamp=None;self.last_confirmed_action=None

    def extract(self,rgb,stamp):
        # 只复制稀疏的真实RGB到内存；单帧路径不载入GPU模型，也不落盘。
        with self.lock:
            if self.closed or not self.available or stamp-self.last_image<200:return None
            if rgb.dtype!=np.uint8 or rgb.ndim!=3 or rgb.shape[2]!=3 or rgb.shape[0]*rgb.shape[1]>1920*1080:
                raise ValueError('视频识别画面无效')
            self.last_image=stamp
            return (stamp,np.ascontiguousarray(rgb).copy())

    def reset(self):
        with self.lock:
            self.generation+=1;self.images.clear();self.rows.clear();self.identity.reset()
            self.job=None;self.completed=None;self.pending=None;self.pending_since=None;self.confirmed=None;self.latest={}
            self.pending_windows=0;self.pending_window_stamp=None;self.last_confirmed_action=None
            self.last_queued=-10000
            self.last_size=None

    def close(self):
        with self.lock:
            self.closed=True;self.reset();self.wake.set()

    def prepare(self):
        with self.lock:
            if self.available and not self.closed:self._start()

    def preparation(self):
        with self.lock:
            return {'state':'unavailable' if not self.available or self.closed else 'ready' if self.model_ready else 'loading',
                'reason':self.reason}

    def _start(self):
        if self.thread is not None:return
        def run():
            try:
                if self.info.get('runtime_cpu_threads'):
                    import torch
                    torch.set_num_threads(int(self.info['runtime_cpu_threads']))
                predictor=self.provider()
                if hasattr(predictor,'warm'):predictor.warm()
                with self.lock:
                    if self.closed:return
                    self.model_ready=True
            except Exception as error:
                logger.exception('连续视频模型准备失败 type=%s',type(error).__name__)
                with self.lock:
                    self.reset();self.available=False;self.reason='semantic_model_unavailable'
                return
            while True:
                self.wake.wait();self.wake.clear()
                with self.lock:
                    if self.closed:return
                    job=self.job;self.job=None
                if job is None:continue
                generation,stamp,images,times,arrays=job
                with self.lock:
                    if self.closed or generation!=self.generation:
                        job=images=times=arrays=None
                        continue
                    self.inference_active=True
                try:
                    result=predictor.predict(images,times,arrays)
                    with self.lock:
                        if not self.closed and generation==self.generation:self.completed=(generation,stamp,result)
                except Exception as error:
                    logger.warning('连续视频识别不可用 type=%s',type(error).__name__)
                    with self.lock:
                        if not self.closed and generation==self.generation:
                            self.reset();self.available=False;self.reason='semantic_inference_unavailable'
                finally:
                    # 显式释放已消费的真实画面，空闲线程不保留最后一个窗口。
                    with self.lock:self.inference_active=False
                    job=images=times=arrays=None
        self.thread=threading.Thread(target=run,name='fitness-video-recognition',daemon=True);self.thread.start()

    def process(self,frame,feature=None):
        with self.lock:
            stamp,seq=frame['captured_ms'],frame['frame_seq']
            if self.closed:return self.snapshot('preview_closed')
            if self.last_stamp is not None and (stamp<=self.last_stamp or seq<=self.last_seq):
                return {**self.snapshot('stale_frame'),'state':'searching','action_id':None,'candidate_action_id':None,'score':None}
            if self.last_stamp is not None and stamp-self.last_stamp>500:self.reset()
            self.last_stamp,self.last_seq=stamp,seq
            if frame['framing'].get('person_count')!=1 or frame['framing'].get('code')=='TARGET_AMBIGUOUS':
                self.reset();return self.snapshot('target_unavailable')
            if not self.identity.accept(frame):self.reset();return self.snapshot('target_changed')
            if not self.available:return self.snapshot(self.reason)
            size=(frame['width'],frame['height'])
            if self.last_size is not None and self.last_size!=size:self.reset()
            self.last_size=size
            self._start()
            self.rows.append((stamp,point_array(frame['framing'].get('landmarks',[])),
                              point_array(frame.get('world_landmarks',[])),(frame['width'],frame['height'])))
            if feature is not None:
                if feature[0]!=stamp or feature[1].shape!=(frame['height'],frame['width'],3):
                    self.reset();return self.snapshot('invalid_visual_feature')
                self.images.append(feature)
            # 推理期间只替换一个待处理窗口，用最新真实图像减掉队列等待；空闲时保留原调用频率。
            queue_step=200 if self.inference_active or self.job is not None else 1500
            if len(self.images)>=8 and self.images[-1][0]-self.images[0][0]>=2800 and stamp-self.last_queued>=queue_step:
                latest=self.images[-1][0];window=[r for r in self.images if latest-r[0]<=CONFIG['maximum_span_ms']]
                times=np.array([r[0] for r in window])
                if len(window)<8:return self.snapshot('warming_up_video')
                desired=np.linspace(max(times[0],times[-1]-3200),times[-1],8)
                indices=np.array([int(np.argmin(np.abs(times-t))) for t in desired])
                if len(set(indices))<8:indices=np.linspace(0,len(window)-1,8).round().astype(int)
                selected=[window[i] for i in indices];times=np.array([r[0] for r in selected]);span=times[-1]-times[0]
                if CONFIG['minimum_span_ms']<=span<=CONFIG['maximum_span_ms'] and stamp-times[-1]<=500:
                    rows=[r for r in self.rows if times[0]<=r[0]<=times[-1]]
                    arrays={'timestamps':np.array([r[0] for r in rows]),'points':np.array([r[1] for r in rows]),
                            'world':np.array([r[2] for r in rows]),'sizes':np.array([r[3] for r in rows]),'people':np.ones(len(rows),int),
                            'inference_mode':'realtime','recognition_hint':self.confirmed or self.pending}
                    self.job=(self.generation,int(times[-1]),[r[1] for r in selected],times,arrays)
                    self.last_queued=stamp;self._start();self.wake.set()
            if self.completed is None:return self.snapshot('warming_up_video')
            generation,source_stamp,result=self.completed
            if generation!=self.generation or not 0<=stamp-source_stamp<=5000:
                self.pending=self.confirmed=None;self.latest={};return self.snapshot('waiting_for_video')
            self.latest={**result,'source_ms':source_stamp,'age_ms':stamp-source_stamp}
            candidate=result['action_id']
            if candidate!=self.pending:
                self.pending=candidate;self.pending_since=stamp;self.confirmed=None
                self.pending_windows=1 if candidate else 0;self.pending_window_stamp=source_stamp
            elif candidate and self.pending_window_stamp!=source_stamp:
                self.pending_windows+=1;self.pending_window_stamp=source_stamp
            if candidate is None:self.confirmed=None
            elif stamp-self.pending_since>=750 and (self.last_confirmed_action in (None,candidate) or self.pending_windows>=2):
                self.confirmed=candidate;self.last_confirmed_action=candidate
            return self.snapshot()

    def snapshot(self,reason=None):
        with self.lock:
            state='recognized' if self.confirmed else 'confirming' if self.pending else 'searching'
            supported=[a for a in LABELS if a]
            return {'mode':'auto','release_mode':self.info.get('release_mode','trained_video_fusion') if self.info else 'trained_video_fusion','state':state,'available':self.available and not self.closed,
                    'action_id':self.confirmed,'candidate_action_id':self.pending,'reason':reason or self.latest.get('reason') or self.reason,
                    'message':'自动识别暂不可用，可使用手动备用' if not self.available else '已识别动作' if self.confirmed else '正在确认动作，请保持自然运动' if self.pending else '尚未确认动作变化，请保持清楚取景并自然完成动作' if self.latest.get('reason')=='motion_unconfirmed' else '正在观察动作',
                    'score':self.latest.get('score'),'score_is_calibrated_probability':False,
                    'model_version':self.info['version'] if self.info else None,'supported_actions':supported if self.available else [],
                    'trained_actions':supported if self.available else [],'target_actions':supported,'zero_shot_actions':[],
                    'manual_fallback_available':True,'decision_basis':self.latest.get('decision_basis'),
                    'source_ms':self.latest.get('source_ms'),'prediction_age_ms':self.latest.get('age_ms'),'motion_evidence':self.latest.get('motion_evidence'),
                    'evidence_level':'development_reference','full_twelve_action_support':self.available,
                    'full_validation_passed':bool(self.info and self.info.get('full_validation_passed'))}
