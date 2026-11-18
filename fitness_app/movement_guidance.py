"""知练同行 连续运动学生物力学评估与动作力线指导模块。

包含双侧相位同步性分析、肘关节漂移度检测、动作节奏平滑与手腕可见力线偏差判定。
"""
from collections import deque
import math
from statistics import median
import numpy as np
from .frame_pipeline import usable_point
from .movement_guidance_policy import RULES,CYCLE_SPECS,SYNC_SPECS,ELBOW_ACTIONS


def angle(a,b,c):
    u=a-b;v=c-b;den=float(np.linalg.norm(u)*np.linalg.norm(v))
    if den<1e-6:return None
    return float(np.degrees(np.arccos(np.clip(u@v/den,-1,1))))


def lag_observation(rows,key):
    if len(rows)<16 or rows[-1]['stamp']-rows[0]['stamp']<2500:return {'status':'unassessable','reason':'awaiting_complete_cycle'}
    times=np.array([r['stamp'] for r in rows]);dt=max(40,int(round(float(np.median(np.diff(times))))))
    if np.max(np.diff(times))>250:return {'status':'unassessable','reason':'cycle_evidence_gap'}
    grid=np.arange(times[0],times[-1]+1,dt);curves=[]
    for side in ('left','right'):
        raw=np.array([r[side][key] for r in rows]);low,high=np.percentile(raw,[5,95])
        if high-low<(.2 if key=='reach' else 25.):return {'status':'unassessable','reason':'awaiting_motion'}
        # 只在本来连续可见的两帧之间重采样，不跨遮挡补点。
        values=np.interp(grid,times,raw);smooth=np.array([np.median(values[max(0,i-2):i+1]) for i in range(len(values))])
        delta=np.diff(smooth)
        if not (np.any(delta>(.015 if key=='reach' else 2.)) and np.any(delta<(-.015 if key=='reach' else -2.))):
            return {'status':'unassessable','reason':'awaiting_complete_cycle'}
        curves.append((smooth-low)/(high-low))
    left,right=curves
    def correlation(a,b):
        if len(a)<12 or min(float(np.std(a)),float(np.std(b)))<.08:return -1.
        return float(np.corrcoef(a,b)[0,1])
    base=correlation(left,right);options=[];maximum=max(1,800//dt)
    for shift in range(-maximum,maximum+1):
        a,b=(left[:-shift],right[shift:]) if shift>0 else (left[-shift:],right[:shift]) if shift<0 else (left,right)
        options.append((correlation(a,b),shift))
    score,shift=max(options,key=lambda item:(item[0],-abs(item[1])))
    delay=shift*dt
    evidence={'lag_ms':delay,'lag_resolution_ms':dt,'aligned_correlation':score,'zero_lag_correlation':base,
              'feature':key,'observed_window_ms':int(times[-1]-times[0])}
    if abs(shift)==maximum or score<.85:return {'status':'unassessable','reason':'phase_signal_uncertain',**evidence}
    if abs(delay)>=250 and score-base>=.2:return {'status':'candidate','side':'right' if delay>0 else 'left',**evidence}
    if base>=.8 and (abs(delay)<250 or score-base<.2):return {'status':'not_observed',**evidence}
    return {'status':'unassessable','reason':'phase_signal_uncertain',**evidence}


class ReturnPhase:
    """因果尖峰抑制与完整回程确认；独立质量阶段，不写训练次数。"""
    def __init__(self,direction):self.direction=direction;self.reset()
    def reset(self):
        self.samples=deque(maxlen=3);self.phase='seeking';self.start=self.peak=self.bottom=None
        self.last_stamp=None;self.maximum_gap=0;self.return_samples=0;self.reason='awaiting_complete_cycle'

    def _event(self,confirmed_by):
        start,peak,bottom=self.start,self.peak,self.bottom
        effort_excursion=peak[1]-start[1];return_excursion=peak[1]-bottom[1]
        effort=peak[0]-start[0];returned=bottom[0]-peak[0]
        if min(effort_excursion,return_excursion)<35 or effort<400 or returned<120 or effort+returned<650:
            self.reason='incomplete_return_evidence';return None
        # 返回接近本次起点才有可比往返；半程反弹不能据耗时判断回程过快。
        if not .8<=return_excursion/effort_excursion<=1.25:
            self.reason='return_range_not_comparable';return None
        if self.return_samples<3:
            self.reason='return_samples_insufficient';return None
        self.reason='awaiting_complete_cycle'
        speed_ratio=(return_excursion/returned)/(effort_excursion/effort)
        return {'start_ms':peak[0],'end_ms':bottom[0],'effort_ms':effort,'return_ms':returned,
            'angle_excursion_degrees':min(effort_excursion,return_excursion),
            'effort_excursion_degrees':effort_excursion,'return_excursion_degrees':return_excursion,
            'return_to_effort_ratio':returned/effort,'return_to_effort_speed_ratio':speed_ratio,
            'maximum_observation_gap_ms':self.maximum_gap,'return_observed_samples':self.return_samples,
            'confirmation_basis':confirmed_by,'fast':returned<450 and speed_ratio>1/.55}

    def update(self,stamp,value):
        if type(stamp) is not int or stamp<0 or type(value) not in (int,float) or not math.isfinite(value):
            self.reset();return None
        if self.last_stamp is not None and (stamp<=self.last_stamp or stamp-self.last_stamp>250):self.reset()
        if self.start is not None and stamp-self.start[0]>12000:self.reset()
        if self.last_stamp is not None:self.maximum_gap=max(self.maximum_gap,stamp-self.last_stamp)
        self.last_stamp=stamp;self.samples.append((stamp,value*self.direction))
        if len(self.samples)==1:self.start=(stamp,value*self.direction)
        if len(self.samples)<3:return None
        first,point,last=self.samples
        # 延迟一帧确认孤立尖峰；保留真实转向极值与原时间，不用滤波延迟估计速度。
        value=point[1]
        if abs(first[1]-last[1])<=10 and abs(value-median((first[1],last[1])))>25:
            value=float(median((first[1],last[1])));point=(point[0],value)
        if self.phase=='seeking':
            if self.start is None or value<=self.start[1]:self.start=point
            if value-self.start[1]>=10:self.phase='effort';self.peak=point
        elif self.phase=='effort':
            if value>=self.peak[1]:self.peak=point
            elif self.peak[1]-value>=10:
                self.phase='return';self.bottom=point;self.return_samples=1
        elif self.phase=='return':
            if value<self.bottom[1]:
                self.bottom=point;self.return_samples+=1
            rebounding=value-self.bottom[1]>=10
            settled=abs(value-self.bottom[1])<=3 and point[0]-self.bottom[0]>=200
            if rebounding or settled:
                event=self._event('next_effort' if rebounding else 'settled_return')
                if rebounding:
                    self.start=self.bottom;self.peak=point;self.bottom=None;self.phase='effort';self.return_samples=0
                    self.maximum_gap=0
                elif event:
                    self.start=self.bottom;self.peak=None;self.bottom=None;self.phase='seeking';self.return_samples=0
                    self.maximum_gap=0
                return event
        return None


def frame_measurements(frame):
    q=frame.get('framing',{});points=q.get('landmarks',[]);basis='mediapipe_original'
    if q.get('person_count')!=1 or not q.get('usable',False) or frame.get('identity_accepted') is False:return {},basis
    data=frame.get('pretrained') or {}
    if data.get('status')=='observed' and data.get('timestamp_ms')==frame.get('source_captured_ms',frame['captured_ms']) and data.get('body'):
        from .rtmpose_input import quality_landmarks
        points=quality_landmarks(data['body']);basis='rtmpose_reference'
    width,height=frame.get('width',0),frame.get('height',0)
    if len(points)!=33 or min(width,height)<=0:return {},basis
    sides={}
    for side,offset in (('left',0),('right',1)):
        ids=[i+offset for i in (11,13,15,23)]
        if any(not usable_point(points[i]) or points[i].get('valid') is False for i in ids):continue
        shoulder,elbow,wrist,hip=[np.array([points[i]['x']*width,points[i]['y']*height]) for i in ids]
        scale=float(np.linalg.norm(shoulder-hip))
        if scale<.08*height:continue
        up=(shoulder-hip)/scale;right=np.array([-up[1],up[0]]);relative=(elbow-shoulder)/scale
        flexion=angle(shoulder,elbow,wrist);arm=angle(elbow,shoulder,hip)
        if flexion is None or arm is None:continue
        sides[side]={'elbow':flexion,'shoulder':arm,'reach':float(np.linalg.norm(wrist-shoulder)/scale),
                     'elbow_x':float(relative@right),'elbow_y':float(relative@up),'scale':scale}
    return sides,basis


def wrist_observation(frame,side):
    data=frame.get('pretrained') or {};stamp=frame.get('source_captured_ms',frame['captured_ms'])
    missing={'status':'unassessable','reason':'wrist_landmarks_unavailable'}
    if data.get('status')!='observed' or data.get('timestamp_ms')!=stamp:return missing
    body=data.get('body',{}).get('keypoints',[]);index=9 if side=='left' else 10;elbow=7 if side=='left' else 8
    if len(body)!=17 or any(not body[i]['coordinate_valid'] or body[i]['score']<.5 for i in (index,elbow)):return missing
    hands=[h for h in data.get('hands',[]) if h.get('body_wrist_index')==index]
    if len(hands)!=1:return missing
    hand=hands[0];points=hand.get('keypoints',[])
    if len(points)!=21 or any(not points[i]['coordinate_valid'] or points[i]['score']<.3 for i in (0,5,9,13)):return missing
    w,h=frame['width'],frame['height'];xy=lambda p:np.array([p['x']*w,p['y']*h])
    wrist=xy(points[0]);palm=np.mean([xy(points[i]) for i in (5,9,13)],axis=0)
    palm_width=float(np.linalg.norm(xy(points[5])-xy(points[13])))
    if palm_width<3 or np.linalg.norm(wrist-xy(body[index]))>max(6.,palm_width*.8):return missing
    forearm=wrist-xy(body[elbow]);palm_axis=palm-wrist
    if min(np.linalg.norm(forearm),np.linalg.norm(palm_axis))<3:return missing
    degrees=angle(wrist-forearm,wrist,palm)
    if degrees is None:return missing
    return {'status':'observed','degrees':180.-degrees,'hand_model_sha256':data.get('hand_model_sha256'),
            'body_model_sha256':data.get('body_model_sha256'),'contact_with_handle_verified':False,
            'hand_localization_basis':hand.get('localization_basis','hand_detector'),
            'crop_agreement_pixels':hand.get('crop_agreement_pixels'),
            'measurement_basis':'同帧主体前臂与可见掌骨方向的二维夹角，不测量握力或接触压力'}


class MovementGuidance:
    def __init__(self,action,version):
        self.action=action;self.version=version;self.rules={c:r for c,r in RULES.items() if r[0]==action};self.reset()
    def reset(self):
        self.rows=deque();self.histories={s:deque() for s in ('left','right')};self.wrists={s:deque() for s in ('left','right')}
        self.phases={s:ReturnPhase(CYCLE_SPECS[self.action][1]) for s in ('left','right')} if self.action in CYCLE_SPECS else {}
        self.last_cycles={};self.states={};self.pending={};self.last_stamp=None;self.basis=None

    def _pack(self,code,status,reason,stamp,side='none',evidence=None,interval=None):
        rule=self.rules[code];identity=(status,reason);key=(code,side)
        old=self.states.get(key)
        if old is None or old[0]!=identity:old=(identity,stamp);self.states[key]=old
        start,end=interval if interval else (old[1],stamp)
        prefix='左侧' if side=='left' else '右侧' if side=='right' else ''
        message=prefix+rule[1] if status=='issue' else '本项未见持续偏差' if status=='not_observed' else {
            'wrist_landmarks_unavailable':'手腕与手掌没有同时看清','simultaneous_variant_required':'本项只适用于双侧同时动作',
            'phase_signal_uncertain':'请完整完成一次双侧动作','unsupported_view':'当前机位不适合判断本项',
            'awaiting_complete_cycle':'等待一次完整往返动作','awaiting_motion':'等待连续动作',
            'incomplete_return_evidence':'请完成充分幅度并保持连续动作',
            'return_range_not_comparable':'本次回程与用力幅度不一致，暂不比较节奏',
            'return_samples_insufficient':'回程阶段的真实观察点不足',
        }.get(reason,'本项缺少连续可见证据')
        item={'code':code,'status':status,'reason':reason,'start_ms':start,'end_ms':end,
            'rule_version':self.version,'evidence_level':'reference','body_parts':list(rule[2]),'message':message,
            'suggestion':rule[5] if status=='issue' else '' if status=='not_observed' else '保持相关部位连续入镜后再检查。',
            'evidence':{'parameter_source':'movement-guidance-development-reference-not-independently-calibrated',
                        'pose_basis':self.basis,**(evidence or {})}}
        if side!='none':item['side']=side
        if status!='issue' and side=='none':
            for other in list(self.states):
                if other[0]==code and other[1]!='none':self.states.pop(other,None)
        return item

    def _confirmed(self,key,candidate,stamp,minimum=600):
        if not candidate:self.pending.pop(key,None);return False
        start=self.pending.setdefault(key,stamp)
        return stamp-start>=minimum

    def update(self,frame,context):
        stamp=frame['captured_ms'];sides,basis=frame_measurements(frame)
        gap=stamp-self.last_stamp if self.last_stamp is not None else None
        if gap is not None and (gap<=0 or gap>500 or self.basis!=basis):self.reset()
        elif gap is not None and gap>250:
            # 稀疏输入可确认持续姿态，但不能证明快速回程或亚秒相位差。
            self.rows.clear();self.last_cycles.clear()
            for phase in self.phases.values():phase.reset()
            for key in list(self.pending):
                if isinstance(key,tuple) and key[0]=='phase_lag':self.pending.pop(key,None)
        self.last_stamp=stamp;self.basis=basis
        row={'stamp':stamp,**sides};self.rows.append(row)
        while self.rows and stamp-self.rows[0]['stamp']>4200:self.rows.popleft()
        for side in ('left','right'):
            if side not in sides:
                self.histories[side].clear();self.wrists[side].clear();self.last_cycles.pop(side,None)
                if side in self.phases:self.phases[side].reset()
                for key in list(self.pending):
                    if isinstance(key,tuple) and side in key:self.pending.pop(key,None)
            else:
                self.histories[side].append({'stamp':stamp,**sides[side]})
                while self.histories[side] and stamp-self.histories[side][0]['stamp']>2400:self.histories[side].popleft()
        items=[];findings=[]
        def append(item,store=True):
            items.append(item)
            if store and item['end_ms']>item['start_ms']:findings.append({**item,'side':item.get('side','none')})
        for code,rule in self.rules.items():
            kind=code.rsplit('.',1)[1]
            if frame.get('posture_view') not in rule[3]:
                if kind=='phase_lag':self.rows.clear()
                if kind=='elbow_drift':
                    for rows in self.histories.values():rows.clear()
                if kind=='wrist_bend':
                    for rows in self.wrists.values():rows.clear()
                if kind=='fast_return':
                    for phase in self.phases.values():phase.reset()
                    self.last_cycles.clear()
                self.pending.clear()
                append(self._pack(code,'unassessable','unsupported_view',stamp));continue
            if kind=='phase_lag':
                if context.get('movement_pattern') in ('alternating','single_side'):
                    self.rows.clear()
                    for key in list(self.pending):
                        if isinstance(key,tuple) and key[0]=='phase_lag':self.pending.pop(key,None)
                    append(self._pack(code,'unassessable','simultaneous_variant_required',stamp));continue
                window=[]
                for r in reversed(self.rows):
                    if 'left' not in r or 'right' not in r:break
                    window.append(r)
                result=lag_observation(list(reversed(window)),SYNC_SPECS[self.action]);status=result['status'];side=result.get('side','none')
                if status=='candidate':
                    candidate_key=('phase_lag',side)
                    for key in list(self.pending):
                        if isinstance(key,tuple) and key[0]=='phase_lag' and key!=candidate_key:self.pending.pop(key,None)
                    status='issue' if self._confirmed(candidate_key,True,stamp,700) else 'unassessable'
                else:
                    for key in list(self.pending):
                        if isinstance(key,tuple) and key[0]=='phase_lag':self.pending.pop(key,None)
                append(self._pack(code,status,result.get('reason') or ('posture_duration_unconfirmed' if status=='unassessable' else ''),stamp,
                                  side if status=='issue' else 'none',result))
            elif kind=='elbow_drift':
                for side in ('left','right'):
                    rows=list(self.histories[side]);evidence={};status='unassessable';reason='required_parts_or_identity_missing'
                    intervals=[b['stamp']-a['stamp'] for a,b in zip(rows,rows[1:])]
                    cadence=median(intervals[-6:]) if intervals else 0
                    minimum_samples=max(4,min(8,math.ceil(1200/cadence)+1)) if cadence else 8
                    if len(rows)>=minimum_samples and stamp-rows[0]['stamp']>=1200:
                        excursion=float(np.percentile([r['elbow'] for r in rows],90)-np.percentile([r['elbow'] for r in rows],10))
                        dx=float(np.percentile([r['elbow_x'] for r in rows],95)-np.percentile([r['elbow_x'] for r in rows],5))
                        dy=float(np.percentile([r['elbow_y'] for r in rows],95)-np.percentile([r['elbow_y'] for r in rows],5))
                        value=math.hypot(dx,dy);moving=excursion>=25
                        confirmed=self._confirmed(('elbow_drift',side),moving and value>.18,stamp)
                        status='issue' if confirmed else 'unassessable' if not moving or value>.18 else 'not_observed'
                        reason='awaiting_motion' if not moving else 'posture_duration_unconfirmed' if status=='unassessable' else ''
                        evidence={'value':value,'threshold':.18,'coordinate_basis':'same_side_shoulder_hip_axes_normalized_by_torso','elbow_excursion_degrees':excursion,
                            'observed_frames':len(rows),'observed_duration_ms':stamp-rows[0]['stamp'],
                            'maximum_observation_gap_ms':max(intervals) if intervals else None}
                    append(self._pack(code,status,reason,stamp,side,evidence))
            elif kind=='fast_return':
                key=CYCLE_SPECS[self.action][0]
                for side in ('left','right'):
                    event=self.phases[side].update(stamp,sides[side][key]) if side in sides else None
                    if event:self.last_cycles[side]=(event,stamp)
                    last=self.last_cycles.get(side)
                    if last and stamp-last[1]<=1500 and side in sides:
                        value=last[0];status='issue' if value['fast'] else 'not_observed'
                        append(self._pack(code,status,'',stamp,side,value,(value['start_ms'],value['end_ms'])),store=event is not None)
                    else:append(self._pack(code,'unassessable',self.phases[side].reason,stamp,side))
            elif kind=='wrist_bend':
                for side in ('left','right'):
                    observed=wrist_observation(frame,side) if side in sides else {'status':'unassessable','reason':'wrist_landmarks_unavailable'}
                    rows=self.wrists[side]
                    if observed['status']!='observed':rows.clear();append(self._pack(code,'unassessable',observed['reason'],stamp,side));continue
                    rows.append((stamp,observed['degrees']))
                    intervals=[b[0]-a[0] for a,b in zip(rows,list(rows)[1:])]
                    cadence=median(intervals[-6:]) if intervals else 0
                    window=max(1000,3*cadence+50) if cadence else 1000
                    while rows and stamp-rows[0][0]>window:rows.popleft()
                    minimum_samples=4 if cadence>250 else 5
                    ready=len(rows)>=minimum_samples and stamp-rows[0][0]>=700;degrees=float(median(v for _,v in rows))
                    bad=degrees>30 and sum(v>25 for _,v in rows)/len(rows)>=.8
                    status='issue' if ready and bad else 'not_observed' if ready and not bad else 'unassessable'
                    append(self._pack(code,status,'posture_duration_unconfirmed' if not ready else '',stamp,side,
                                      {**observed,'value':degrees,'threshold':30.,'observed_duration_ms':stamp-rows[0][0],
                                       'observed_frames':len(rows),'maximum_observation_gap_ms':max(intervals) if intervals else None}))
        return items,findings
