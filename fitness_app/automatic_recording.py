"""自动识别片段的持久化适配器；复用已有记录格式与幂等写入，失败冻结原批次。"""
import copy
import uuid
import logging
from .catalogue import get_action
logger=logging.getLogger(__name__)

class AutomaticRecorder:
    def __init__(self,store,user_id,auth_id,run_id,retest_origin=None,save_skeleton=False):
        self.store,self.user_id,self.auth_id,self.run_id=store,user_id,auth_id,run_id
        self.retest_origin=copy.deepcopy(retest_origin)
        self.save_skeleton=save_skeleton;self.skeleton=None;self.last_skeleton=None
        self.stability=None;self.last_stability=None
        self.record_ids=[];self.current=None;self.pending=None;self.failed=False;self.message=None;self.closing=None

    @staticmethod
    def key(segment,label):return str(uuid.uuid5(uuid.UUID(segment),label))

    def snapshot(self):
        return {'save_state':'save_failed' if self.failed else 'clean','saved_record_ids':list(self.record_ids),
                'saved_record_id':self.current.get('record_id') if self.current else None,
                'run_id':self.run_id,'message':self.message,'pending':self.pending is not None or self.closing is not None,
                'skeleton':self.skeleton.snapshot() if self.skeleton else self.last_skeleton,
                'stability':self.stability.snapshot() if self.stability else self.last_stability}

    def process(self,analysis,output,frame):
        if self.failed:return self.snapshot()
        if self.current is None:
            self.current={'segment_id':analysis.segment_id,'action_id':analysis.action,'offset':frame['captured_ms'],
                          'model_version':analysis.version,'record_id':None,'active_ms':0,'next_seq':1,
                          'coaching_view':getattr(analysis,'coaching_view','auto'),'exercise_basis':getattr(analysis,'basis','automatically_recognized')}
        self.current['active_ms']=analysis.active_ms
        if not any(output.get(k) for k in ('events','intervals','posture_findings')):return self.snapshot()
        self.pending=copy.deepcopy(output)
        for index,event in enumerate(self.pending.get('events',[])):
            event.update(event_seq=self.current['next_seq']+index,set_index=1)
        try:self._flush()
        except Exception as error:self._failure(error)
        return self.snapshot()

    def _failure(self,error):
        logger.warning('自动训练保存冻结 run=%s type=%s code=%s',self.run_id,type(error).__name__,getattr(error,'code','storage'))
        self.failed=True
        self.message='本段结果尚未全部保存，分析已冻结。请重试保存或保留已保存内容。'
        self.error_type=type(error).__name__
        self.error_code=getattr(error,'code',self.error_type)
        self.error_detail=str(error)

    def capture(self,frame,coaching=None,output=None):
        if self.failed or self.current is None:return
        if self.stability is None:
            from .stability_store import StabilityCapture,StabilityStore
            self.stability=StabilityCapture(StabilityStore(self.store),self.user_id,self.current['action_id'],offset=self.current['offset'])
        if self.current.get('record_id') and not self.stability.sid:self.stability.bind(self.current['record_id'])
        self.stability.push(frame,output or {},1,self.current['segment_id'])
        if not self.save_skeleton:return
        if self.skeleton is None:
            from .skeleton_store import SkeletonBuffer,SkeletonStore
            self.skeleton=SkeletonBuffer(SkeletonStore(self.store),self.user_id,offset=self.current['offset'])
        if self.current.get('record_id') and not self.skeleton.sid:self.skeleton.bind(self.current['record_id'])
        self.skeleton.push(frame,coaching)

    def _flush(self):
        cur=self.current
        if self.pending is None:return
        if cur['record_id'] is None:
            if self.retest_origin:
                from .retests import Retests
                Retests(self.store).prepare(self.user_id,self.auth_id,self.retest_origin)
            action=get_action(cur['action_id'])
            config={'action_id':cur['action_id'],'capability_version':action['capability_version'],'source':'camera',
                    'mode':'free','target':None,'recognition_mode':'manual' if cur['model_version']=='manual-selection' else 'auto','automatic_run_id':self.run_id,
                    'automatic_segment_id':cur['segment_id'],'recognition_model_version':cur['model_version'],
                    'coaching_view':cur['coaching_view'],'exercise_basis':cur['exercise_basis']}
            if self.retest_origin:config['retest_origin']=copy.deepcopy(self.retest_origin)
            if self.save_skeleton:config['save_skeleton']=True
            result=self.store.create(self.user_id,self.auth_id,config,self.key(cur['segment_id'],'create'),stability=True)
            cur['record_id']=result['session_id']
            if result['session_id'] not in self.record_ids:self.record_ids.append(result['session_id'])
        sid=cur['record_id']
        self.store.set_state(self.user_id,sid,'running',self.key(cur['segment_id'],'start'),auth_id=self.auth_id)
        def convert(item):
            item=copy.deepcopy(item)
            for name in ('start_ms','end_ms'):
                if name in item:item[name]=max(0,item[name]-cur['offset'])
            if isinstance(item.get('evidence'),dict):item['evidence']['time_base']='session_elapsed_ms'
            return item
        for event in self.pending.get('events',[]):
            event=convert(event);event['assessments']=[convert(x) for x in event.get('assessments',[])]
            event['active_ms']=cur['active_ms']
            self.store.append_event(self.user_id,sid,event,self.auth_id)
        intervals=[convert(x) for x in self.pending.get('intervals',[])]
        if intervals:self.store.append_intervals(self.user_id,sid,1,intervals,self.auth_id,cur['active_ms'])
        findings=[convert(x) for x in self.pending.get('posture_findings',[])]
        if findings:self.store.append_posture(self.user_id,sid,1,findings,self.auth_id,cur['active_ms'])
        cur['next_seq']+=len(self.pending.get('events',[]))
        self.pending=None;self.failed=False;self.message=None

    def close(self,reason):
        if self.current is None:return
        self.closing=reason
        if self.failed:return
        try:
            self._flush()
            cur=self.current
            if cur['record_id']:
                # 系统收尾不依赖已过期/撤销的旧登录，已提交部分仍归原用户。
                self.store.set_state(self.user_id,cur['record_id'],'interrupted' if reason in {'auth_revoked','auth_expired','service_stopped','control_lost'} else 'ended_early',
                    self.key(cur['segment_id'],'close'),auth_id=self.auth_id,reason=reason,
                    extra={'active_ms':cur['active_ms']},system=reason in {'auth_revoked','auth_expired','service_stopped','control_lost'})
            if self.skeleton:
                if cur['record_id'] and not self.skeleton.sid:self.skeleton.bind(cur['record_id'])
                self.skeleton.close(reason in {'auth_revoked','auth_expired','service_stopped','control_lost'})
                self.last_skeleton=self.skeleton.snapshot();self.skeleton=None
            if self.stability:
                if cur['record_id'] and not self.stability.sid:self.stability.bind(cur['record_id'])
                self.stability.close(reason in {'auth_revoked','auth_expired','service_stopped','control_lost'})
                self.last_stability=self.stability.snapshot();self.stability=None
            self.current=None;self.closing=None
        except Exception as error:self._failure(error)

    def recover(self,auth_id,discard=False):
        self.auth_id=auth_id
        if discard:self.pending=None
        self.failed=False;self.message=None
        try:
            self._flush()
            if self.current:self.close(self.closing or ('control_lost' if discard else 'save_recovered'))
        except Exception as error:self._failure(error)
        return self.snapshot()
