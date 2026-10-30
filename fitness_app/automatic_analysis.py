"""自动确认后的内存姿态分析；片段隔离，不将预览冒充已保存训练。"""
from .coaching import coaching_observation,coaching_snapshot,committed_issues
from .db_schema import new_id
from .posture_engine import create_analysis_engine
from .posture_rules import rules_for


class AutomaticAnalysis:
    def __init__(self,engine_provider=create_analysis_engine,recorder=None,coaching_view='auto',focus_code=None):
        if not isinstance(coaching_view,str) or coaching_view not in {'auto','front','side','oblique'}:raise ValueError('机位设置无效')
        self.coaching_view=coaching_view
        self.focus_code=focus_code
        self.recorder=recorder
        self.engine_provider=engine_provider;self.engine=None;self.action=None;self.version=None
        self.segment_id=None;self.segment_index=0;self.last_seq=None;self.last_stamp=None
        self.feedback=[];self.feedback_stamp=None;self.active_ms=0;self.events=0;self.hold_ms=0

    def reset(self,reason):
        if self.recorder:self.recorder.close(reason)
        if self.engine:self.engine.reset(reason)
        self.engine=None;self.action=None;self.version=None;self.segment_id=None
        self.feedback=[];self.feedback_stamp=None;self.active_ms=0;self.events=0;self.hold_ms=0

    def process(self,frame,recognition):
        stamp,seq=frame['captured_ms'],frame['frame_seq']
        previous=self.last_stamp
        reason=None
        if self.last_seq is not None and (seq<=self.last_seq or stamp<=self.last_stamp):
            self.reset('stale_frame');return self.waiting(frame,recognition,'stale_frame')
        self.last_seq,self.last_stamp=seq,stamp
        if previous is not None and stamp-previous>500:self.reset('source_gap')
        action=recognition.get('action_id')
        if not recognition.get('available') or recognition.get('state')!='recognized' or not action:
            reason=recognition.get('reason') or 'action_unconfirmed'
        elif not rules_for(action):reason='unsupported_action'
        elif frame.get('identity_accepted') is False or not frame['framing'].get('usable'):
            reason='positioning'
        elif frame['framing'].get('person_count')!=1 or frame['framing'].get('code')=='TARGET_AMBIGUOUS':
            reason='target_unavailable'
        if reason:
            self.reset(reason);return self.waiting(frame,recognition,reason)
        if self.action!=action or self.version!=recognition.get('model_version'):
            self.reset('action_or_model_changed')
        if self.engine is None:
            self.engine=self.engine_provider(action)
            if self.engine is None:return self.waiting(frame,recognition,'analysis_unavailable')
            self.action=action;self.version=recognition.get('model_version')
            self.segment_id=new_id();self.segment_index+=1
        elif previous is not None:self.active_ms+=stamp-previous
        self.basis='user_selected' if recognition.get('decision_basis')=='user_selected' else 'automatically_recognized'
        context={'session_id':self.segment_id,'stream_epoch':self.segment_id,'set_index':1,
                 'active_ms':self.active_ms,'time_base':'source_ms','coaching_view':self.coaching_view,
                 'exercise_basis':self.basis,'recognition_model_version':self.version}
        output=self.engine.process(frame,context)
        analysis=coaching_observation(output)
        # 调用者拿到的所有证据都明确动作来自分类器，而非用户已确认变式。
        for items in (analysis.get('posture',{}).get('items',[]),output.get('posture_findings',[])):
            for item in items:self.label_evidence(item)
        for event in output.get('events',[]):
            for item in event.get('assessments',[]):self.label_evidence(item)
        issues=committed_issues(output)
        withdrawn=[item for item in analysis.get('posture',{}).get('items',[]) if item.get('status') in {'not_observed','unassessable'}]
        self.feedback=[item for item in self.feedback if not any(
            other.get('code')==item.get('code') and ('side' not in other or other['side']==item.get('side','none'))
            for other in withdrawn)]
        if issues:
            self.feedback=issues[:32];self.feedback_stamp=stamp
        self.events+=len(output.get('events',[]))
        self.hold_ms+=sum(i['end_ms']-i['start_ms'] for i in output.get('intervals',[]) if i.get('kind')=='hold')
        saved=self.recorder.process(self,output,frame) if self.recorder else None
        session={'action_id':action,'status':'running','supported_items':rules_for(action),
                 'exercise_basis':self.basis}
        focused=analysis
        if self.focus_code:
            session['supported_items']=[v for v in session['supported_items'] if v['code']==self.focus_code]
            focused={**analysis,'posture':{**analysis.get('posture',{}),'items':[v for v in analysis.get('posture',{}).get('items',[]) if v.get('code')==self.focus_code]}}
        coaching=coaching_snapshot(session,{**frame,'analysis':focused},self.feedback,
            stamp-self.feedback_stamp if self.feedback_stamp is not None else 0)
        coaching.update(persistence='memory_only',recognition_model_version=self.version)
        coaching['recognition_basis']=recognition.get('decision_basis','trained_classifier')
        if self.recorder:
            self.recorder.capture(frame,coaching,output);saved=self.recorder.snapshot()
        if saved:
            coaching['persistence']='save_failed' if saved['save_state']=='save_failed' else 'saved_reference'
            if saved['save_state']=='save_failed':coaching.update(state='save_failed',message=saved['message'],primary_cue=None)
        return {'analysis':analysis,'coaching':coaching,'automatic_analysis':{
            'state':'analyzing','reason':None,'action_id':action,'segment_id':self.segment_id,
            'retest_focus_code':self.focus_code,
            'segment_index':self.segment_index,'frame_seq':seq,'captured_ms':stamp,'time_base':'source_ms',
            'recognition_model_version':self.version,'counting_available':self.engine.counting_available,
            'reference_completed_cycles':self.events if self.engine.counting_available else None,
            'reference_hold_ms':self.hold_ms if action=='forearm_plank' else None,
            'persistence':coaching['persistence'],'saved_record_id':saved['saved_record_id'] if saved else None,'evidence_level':'reference'}}

    def label_evidence(self,item):
        item.setdefault('evidence',{}).update(exercise_basis=getattr(self,'basis','automatically_recognized'),
            recognition_model_version=self.version,variant_confirmation='automatically_inferred',time_base='source_ms')

    def waiting(self,frame,recognition,reason):
        positioning=reason in {'positioning','target_unavailable','target_changed'}
        state='positioning' if positioning else 'waiting_for_recognition'
        message='需要调整取景' if positioning else recognition.get('message','正在观察动作')
        instruction='让当前动作所需的身体部位连续入镜。' if positioning else '动作确认后自动开始姿态分析。'
        if reason=='stale_frame':state='waiting_for_input';message='等待新的画面';instruction='检查视频输入是否仍在更新。'
        elif reason=='target_unavailable':
            state='no_person' if frame['framing'].get('person_count')==0 else 'multiple_people'
            message='还没有识别到训练者' if state=='no_person' else '训练目标不确定'
            instruction='让本次训练者单独入镜，等待重新确认。'
        elif reason=='target_changed':message='训练目标发生变化，请重新稳定取景'
        return {'analysis':{},'automatic_analysis':{
            'state':'positioning' if positioning else 'waiting_for_recognition','reason':reason,
            'action_id':None,'segment_id':None,'segment_index':self.segment_index,
            'frame_seq':frame['frame_seq'],'captured_ms':frame['captured_ms'],'time_base':'source_ms',
            'recognition_model_version':recognition.get('model_version'),'counting_available':False,
            'reference_completed_cycles':None,'reference_hold_ms':None,'persistence':'memory_only','saved_record_id':None},
            'coaching':{'schema_version':1,'state':state,'message':message,'instruction':instruction,
                'frame_seq':frame['frame_seq'],'frame_captured_ms':frame['captured_ms'],
                'recommended_views':[],'phases':[],'other_cue_count':0,'assessment_scope':'supported_items_only',
                'recommended_prompt_cooldown_ms':5000,
                'action_id':None,'exercise_basis':'automatically_recognized','primary_cue':None,
                'overall_correctness':None,'persistence':'memory_only','evidence_level':'reference'}}
