"""新增器械动作的投影姿态参考；没有周期真值时不产出次数或整体正确率。"""
from .gym_motion import measurements,quantile,span
from .pretrained_policy import RULES as PRETRAINED_RULES
from .movement_guidance_policy import RULES as GUIDANCE_RULES


def evaluate(code,frames,view,threshold,*,require_continuous=False,allowed_views=None):
    missing={'status':'unassessable','reason':'required_parts_or_identity_missing'}
    values=measurements(frames)
    if not values:return missing
    # 自动估计在规则允许的侧面/斜侧面之间变化，不是用户声明的机位冲突。
    permitted=set(allowed_views or (view,))
    if any(f.get('posture_view',view) not in permitted for f in frames):return {'status':'unassessable','reason':'unsupported_view'}
    if code.endswith('arm_asymmetry'):
        if code=='pec_deck_fly.arm_asymmetry' and view=='oblique':
            from .posture_engine import view_ratio
            ratio=view_ratio(frames[-1])
            # 近正面且双侧可见时可比较相对肩部的手臂高度；明显侧面仍不比较。
            if ratio is None or ratio<.6:return {'status':'unassessable','reason':'unsupported_view'}
        # 分类测量允许短暂缺点；当前纠错必须两侧可见，并重新积累遮挡后的连续证据。
        visible={row['stamp'] for row in values['bilateral']}
        continuous=0
        for frame in reversed(frames):
            if frame['captured_ms'] not in visible:break
            continuous+=1
        if not continuous:return missing
        values=measurements(frames[-continuous:])
        if not values or not values['bilateral']:return missing
    elif require_continuous:
        # 只使用这一侧遮挡后重新积累的连续窗口，不将遮挡前后的摆幅拼接。
        sides={}
        for side,rows in values['sides'].items():
            visible={row['stamp'] for row in rows};continuous=0
            for frame in reversed(frames):
                if frame['captured_ms'] not in visible:break
                continuous+=1
            if continuous<8:continue
            start=frames[-continuous]['captured_ms']
            current=[row for row in rows if row['stamp']>=start]
            if current[-1]['stamp']-current[0]['stamp']>=1200:sides[side]=current
        if not sides:return missing
        values={**values,'sides':sides}
    else:
        current=frames[-1]['captured_ms']
        sides={side:rows for side,rows in values['sides'].items() if rows[-1]['stamp']==current}
        if not sides:return missing
        values={**values,'sides':sides}
    if not any(max(span(rows,'elbow'),span(rows,'arm'))>12 for rows in values['sides'].values()):
        return {'status':'unassessable','reason':'awaiting_motion'}
    if code.endswith('arm_asymmetry'):
        rows=values['bilateral']
        if not rows:return missing
        # 连续至少700ms，不将一次抖点或遮挡前后的片段拼成偏差。
        longest=0;start=None;last=None
        for row in rows:
            if row['height_difference']>threshold:
                if start is None or last is not None and row['stamp']-last>250:start=row['stamp']
                longest=max(longest,row['stamp']-start)
            else:start=None
            last=row['stamp']
        value=quantile(rows,'height_difference',90);issue=longest>=700;feature='bilateral_wrist_height_difference_over_torso'
    else:
        key='trunk' if code.endswith('trunk_sway') else 'arm'
        ranges={side:span(rows,key) for side,rows in values['sides'].items()}
        if key=='trunk' and len(ranges)==2 and abs(ranges['left']-ranges['right'])>12:
            return {'status':'unassessable','reason':'bilateral_projection_disagreement'}
        value=max(ranges.values());issue=value>threshold;feature=key+'_image_angle_range'
    return {'status':'issue' if issue else 'not_observed','reason':None,'value':value,'threshold':threshold,
            'feature_name':feature,'measurement_basis':'连续窗口可见关节的图像投影；不能用于肌肉受力判断'}


def observe(engine,frame,view,basis,codes=None):
    stamp=frame['captured_ms'];frames=[f for f in engine.frames if stamp-f['captured_ms']<=2400]
    items=[];findings=[]
    for rule in engine.rules:
        if rule['code'] in PRETRAINED_RULES or rule['code'] in GUIDANCE_RULES:continue
        if codes is not None and rule['code'] not in codes:continue
        if rule['code'].endswith('trunk_lateral_lean'):
            from .scene_reference import evaluate_lateral
            result=evaluate_lateral(frames,rule['threshold'],rule['views'],engine.static.get(rule['code']))
        else:
            result=evaluate(rule['code'],frames,view,rule['threshold'],require_continuous=engine.action=='standing_dumbbell_curl',allowed_views=rule['views']) if view in rule['views'] else {'status':'unassessable','reason':'unsupported_view'}
        identity=(result['status'],result.get('reason'),result.get('side','none'))
        previous=engine.static.get(rule['code'])
        if previous is None or previous['identity']!=identity:
            previous=engine.static[rule['code']]={'identity':identity,'start':stamp,'side':result.get('side','none')}
        item=engine._pack(result,rule['code'],{'start_ms':previous['start'],'end_ms':stamp},view,basis)
        item['side']=result.get('side','none')
        if rule['code'].endswith('trunk_lateral_lean') and item['status']=='issue':
            item['message']='躯干持续向'+('左' if item['side']=='left' else '右')+'侧偏斜'
        items.append(item)
        if stamp>previous['start']:findings.append(item)
    return items,findings
