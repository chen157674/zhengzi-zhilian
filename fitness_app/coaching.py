"""识别状态与实时动作指导提示模块。

参考新上游的分层状态、主要／次要问题组织，采用本项目的时间、保存及侧别
契约；不移植RDL变式阈值，不根据空反馈判断整体正确。
"""
import hashlib
import json
import copy

MAX_FEEDBACK_AGE_MS=2500
ALIGNMENT={'plank.hip_high','plank.hip_low','push_up.hip_high','push_up.hip_low','barbell_row.trunk','overhead_press.elbow',
           'machine_chest_press.pretrained_upper_body_balance'}
STATES={
    'save_failed':('结果尚未保存','先重试保存或处理保存故障，再继续训练。'),
    'ended':('本次分析已结束','可在个人记录查看已保存的动作和姿态反馈。'),
    'interrupted':('本次分析已中断','先检查输入或处理错误，已保存的结果可在个人记录查看。'),
    'paused':('已暂停','准备好后继续，暂停期间不会累计动作结果。'),
    'resting':('组间休息','调整呼吸，准备下一组。'),
    'preparing':('正在准备','按所选动作的起始姿势准备，让相关部位连续入镜。'),
    'no_person':('还没有识别到训练者','站到镜头可见范围，让所需身体部位入镜。'),
    'multiple_people':('训练目标不确定','画面中只保留本次训练者，重新稳定取景。'),
    'positioning':('需要调整取景','调整机位，使当前判项需要的部位连续可见。'),
    'waiting_for_input':('正在等待新的画面','检查视频输入或摄像头连接，恢复后再继续。'),
    'moving':('正在观察动作','保持可控制的节奏，完成起始、动作和返回。'),
    'ready_to_move':('已识别起始姿态','可以开始完成一次完整动作。'),
    'evidence_insufficient':('暂时无法判断这一项','保持相关部位可见，按推荐机位完成完整动作后再观察。'),
    'observing':('正在观察姿态','当前已观察范围未发现该项偏差，继续平稳完成动作。'),
    'waiting_for_cycle':('等待一次完整动作','完成动作和返回后，系统会更新本次可评估的项目。'),
}


def compact_frame(frame,observation=None):
    """视频任务只保留状态所需字段，不在任务快照存骨架或图像。"""
    return {'frame_seq':frame.get('frame_seq'),'captured_ms':frame.get('captured_ms'),
            'framing':{k:frame.get('framing',{}).get(k) for k in ('usable','code','person_count','message','posture_scope')},
            'analysis':observation or frame.get('analysis',{})}


def committed_issues(output):
    items=list(output.get('posture_findings',[]))
    for event in output.get('events',[]):
        items.extend({**item,'side':event.get('side','none'),'kind':'cycle'} for item in event.get('assessments',[]))
    return [i for i in items if i.get('status')=='issue']


def coaching_observation(output):
    """周期判项的侧别来自其所属事件；不能丢失后将另一侧的结果当成撤回依据。"""
    observation=copy.deepcopy(output.get('observation',{}))
    assessed=[{**item,'side':event.get('side','none')} for event in output.get('events',[])
              for item in event.get('assessments',[])]
    if assessed:
        posture=observation.setdefault('posture',{})
        assessed_codes={item['code'] for item in assessed}
        posture['items']=[item for item in posture.get('items',[]) if item.get('code') not in assessed_codes]+assessed
    return observation


def coaching_snapshot(session,frame=None,feedback=None,feedback_age_ms=0,frame_fresh=True):
    """组织调用方确认的问题；会话须已保存，自动预览须显式标记memory_only。

    读取／轮询不产生证据或改变确认时间。
    """
    q=(frame or {}).get('framing',{});analysis=(frame or {}).get('analysis',{});posture=analysis.get('posture',{})
    rules=session.get('supported_items',[]);views=sorted({view for rule in rules for view in rule.get('views',[])})
    phases=sorted({v.get('phase') for v in analysis.get('sides',{}).values() if v.get('phase')})
    result={'schema_version':1,'exercise_basis':session.get('exercise_basis','user_selected'),'action_id':session.get('action_id'),
            'primary_cue':None,'other_cue_count':0,'recommended_views':views,'phases':phases,
            'assessment_scope':q.get('posture_scope') or 'supported_items_only','overall_correctness':None,
            'frame_seq':(frame or {}).get('frame_seq'),'frame_captured_ms':(frame or {}).get('captured_ms'),
            'recommended_prompt_cooldown_ms':5000,'evidence_level':'reference'}
    state=session.get('status')
    if session.get('save_state','clean')!='clean' or session.get('analysis_frozen'):code='save_failed'
    elif state in {'interrupted','failed'}:code='interrupted'
    elif state in {'completed','ended_early','ending'}:code='ended'
    elif state in {'paused','resting','ready','countdown'}:code={'ready':'preparing','countdown':'preparing'}.get(state,state)
    elif not frame or not frame_fresh:code='waiting_for_input'
    elif q.get('code')=='NO_PERSON':code='no_person'
    elif q.get('code')=='TARGET_AMBIGUOUS':code='multiple_people'
    elif not q.get('usable'):code='positioning'
    elif views and posture.get('view') is not None and posture['view'] not in views:code='positioning'
    else:
        supported={r['code'] for r in rules}
        items=posture.get('items',[])
        withdrawn=[i for i in items if i.get('status') in {'not_observed','unassessable'}]
        def superseded(feedback):
            key=feedback.get('code') or feedback.get('prompt_id')
            return any(i.get('code')==key and ('side' not in i or i['side']==feedback.get('side','none')) for i in withdrawn)
        candidates=[f for f in feedback or [] if (f.get('code') or f.get('prompt_id')) in supported and not superseded(f)] if feedback_age_ms<=MAX_FEEDBACK_AGE_MS else []
        if candidates:
            candidates.sort(key=lambda f:(0 if (f.get('code') or f.get('prompt_id')) in ALIGNMENT else 1,f.get('code') or f.get('prompt_id'),f.get('side','none')))
            selected=candidates[0];key=selected.get('code') or selected['prompt_id']
            cue={k:selected.get(k) for k in ('message','suggestion','side','body_parts','start_ms','end_ms','rule_version','evidence')}
            cue.update(code=key,evidence_level=selected.get('evidence_level','reference'))
            identity=json.dumps({k:cue.get(k) for k in ('code','side','start_ms','rule_version','message')},sort_keys=True,ensure_ascii=False)
            cue['cue_id']=hashlib.sha256(identity.encode()).hexdigest()[:20]
            result.update(state='needs_adjustment',message=cue['message'],instruction=cue['suggestion'],primary_cue=cue,other_cue_count=len(candidates)-1)
            return result
        if items and all(i.get('status')=='unassessable' for i in items):code='evidence_insufficient'
        elif any(i.get('status')=='not_observed' for i in items):code='observing'
        elif any(p in {'departing','moving','returning','confirming_return'} for p in phases):code='moving'
        elif 'ready' in phases:code='ready_to_move'
        else:code='waiting_for_cycle'
    message,instruction=STATES[code]
    if code=='evidence_insufficient' and posture.get('items'):
        message=posture['items'][0].get('message') or message
        instruction=posture['items'][0].get('suggestion') or instruction
    result.update(state=code,message=message,instruction=instruction)
    return result
