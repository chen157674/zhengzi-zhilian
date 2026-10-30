"""离线动作窗口的时间分段；没有可信动作的区间保留未知，不用纠错事件决定动作。"""
from .catalogue import definitions

POLICY_VERSION='video-segments-2'
POLICY={'initial_confirm_windows':1,'switch_confirm_windows':2,'maximum_tail_carry_ms':1600,
        'hard_gap_ms':500,'change_boundary':'first_candidate_window_midpoint',
        'nonoverlapping_change_boundary':'first_candidate_window_start',
        'initial_boundary':'first_accepted_window_start','time_base':'source_ms','interval':'half_open'}


def _integer(value,field,maximum=1800000):
    if type(value) is not int or not 0<=value<=maximum:raise ValueError(field+'无效')
    return value


def _gaps(raw,duration):
    if raw is None:return []
    if not isinstance(raw,list):raise ValueError('不可评估区间无效')
    values=[]
    for item in raw:
        if not isinstance(item,dict):raise ValueError('不可评估区间无效')
        start=_integer(item.get('start_ms'),'start_ms');end=_integer(item.get('end_ms'),'end_ms')
        if not start<end<=duration:raise ValueError('不可评估区间越界')
        reason=item.get('reason','target_unavailable')
        if reason not in {'target_unavailable','source_gap','target_changed','missing_parts'}:raise ValueError('不可评估原因无效')
        values.append({'start_ms':start,'end_ms':end,'reason':reason})
    values.sort(key=lambda x:(x['start_ms'],x['end_ms']));merged=[]
    for item in values:
        if merged and item['start_ms']<=merged[-1]['end_ms']:
            last=merged[-1];last['end_ms']=max(last['end_ms'],item['end_ms'])
            if last['reason']!=item['reason']:last['reason']='target_unavailable'
        else:merged.append(dict(item))
    return merged


def segment_decisions(decisions,duration_ms,unassessable=None):
    """返回覆盖整个已解码源范围的连续分区；窗口仅来自服务端已确认的分类结果。

    该函数定义工程边界，不能把合成窗口测试当作实际视频分段准确率。
    """
    duration=_integer(duration_ms,'duration_ms')
    if duration==0:raise ValueError('源范围不能为空')
    if not isinstance(decisions,list):raise ValueError('识别窗口必须为数组')
    allowed={v['action_id'] for v in definitions()};windows=[];last_end=-1;version=None
    for item in decisions:
        if not isinstance(item,dict):raise ValueError('识别窗口无效')
        start=_integer(item.get('start_ms'),'start_ms');end=_integer(item.get('end_ms'),'end_ms')
        if not start<end<=duration or end<=last_end:raise ValueError('识别窗口越界或时间未递增')
        action=item.get('action_id')
        if action is not None and (not isinstance(action,str) or action not in allowed):raise ValueError('动作类别无效')
        model=item.get('model_version')
        if not isinstance(model,str) or not 1<=len(model)<=200:raise ValueError('识别模型身份无效')
        if version is not None and model!=version:raise ValueError('单个视频不能混用识别模型版本')
        version=model;last_end=end
        windows.append({'start_ms':start,'end_ms':end,'action_id':action,'model_version':model})
    gaps=_gaps(unassessable,duration);segments=[];state=None;began=0;pending=None;pending_start=0;pending_count=0;ever_accepted=False;last_state_end=None

    def append(start,end,action,reason=None):
        if end<=start:return
        segments.append({'start_ms':start,'end_ms':end,'action_id':action,'kind':'action' if action else 'unknown',
                         'reason':reason if action is None else None,'model_version':version,
                         'decision_basis':'automatically_recognized' if action else 'insufficient_action_evidence'})

    for window in windows:
        candidate=window['action_id']
        if candidate==state:
            pending=None;pending_count=0;last_state_end=window['end_ms'];continue
        if pending_count and candidate==pending:pending_count+=1
        else:
            pending=candidate;pending_count=1;pending_start=window['start_ms'];pending_mid=(window['start_ms']+window['end_ms'])//2
            pending_after_evidence_gap=last_state_end is not None and pending_start>=last_state_end
        needed=POLICY['initial_confirm_windows'] if not ever_accepted and candidate else POLICY['switch_confirm_windows']
        if pending_count<needed:continue
        boundary=pending_start if (not ever_accepted and candidate) or pending_after_evidence_gap else pending_mid
        boundary=max(began,min(duration,boundary))
        append(began,boundary,state,'warming_up_or_unconfirmed' if not ever_accepted else 'action_unconfirmed')
        began=boundary;state=candidate;ever_accepted=ever_accepted or candidate is not None
        pending=None;pending_count=0;last_state_end=window['end_ms']
    tail=windows[-1]['end_ms'] if windows else 0
    if state and duration-tail>POLICY['maximum_tail_carry_ms']:
        append(began,tail,state);append(tail,duration,None,'tail_without_recognition')
    else:append(began,duration,state,'action_unconfirmed' if windows else 'insufficient_video_context')

    # 长缺口显式未知；短缺口保留为可评估覆盖缺失，避免单帧抖动制造一串动作片段。
    result=[]
    for original in segments:
        pieces=[original]
        for gap in gaps:
            if gap['end_ms']-gap['start_ms']<POLICY['hard_gap_ms'] and gap['reason']!='target_changed':continue
            updated=[]
            for piece in pieces:
                start=max(piece['start_ms'],gap['start_ms']);end=min(piece['end_ms'],gap['end_ms'])
                if end<=start:updated.append(piece);continue
                if piece['start_ms']<start:updated.append({**piece,'end_ms':start})
                updated.append({**piece,'start_ms':start,'end_ms':end,'action_id':None,'kind':'unknown',
                    'reason':gap['reason'],'decision_basis':'unassessable_source_range'})
                if end<piece['end_ms']:updated.append({**piece,'start_ms':end})
            pieces=updated
        result.extend(pieces)
    merged=[]
    keys=('action_id','kind','reason','model_version','decision_basis')
    for piece in result:
        if merged and merged[-1]['end_ms']==piece['start_ms'] and all(merged[-1][k]==piece[k] for k in keys):merged[-1]['end_ms']=piece['end_ms']
        else:merged.append(dict(piece))
    for index,piece in enumerate(merged,1):
        piece.update(segment_index=index,policy_version=POLICY_VERSION,time_base='source_ms',interval='half_open')
        piece['unassessable_ranges']=[{'start_ms':max(piece['start_ms'],gap['start_ms']),
            'end_ms':min(piece['end_ms'],gap['end_ms']),'reason':gap['reason']} for gap in gaps
            if gap['start_ms']<piece['end_ms'] and gap['end_ms']>piece['start_ms']]
        # 类别迟滞允许保持片段结构，但未知窗口绝不能继续沿用上一动作的纠错结论。
        piece['recognition_unassessable_ranges']=[{'start_ms':max(piece['start_ms'],w['start_ms']),
            'end_ms':min(piece['end_ms'],w['end_ms']),'reason':'action_unconfirmed'} for w in windows
            if w['action_id']!=piece['action_id'] and w['start_ms']<piece['end_ms'] and w['end_ms']>piece['start_ms']]
    return merged
