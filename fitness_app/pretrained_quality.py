"""预训练观察的连续确认与拒判；与原计数器独立，不把类别分数当完整正确率。"""
from collections import deque
import math
from statistics import median
from .pretrained_policy import RULES


class PretrainedObserver:
    def __init__(self,action,version,codes=None):
        self.action=action;self.version=version;self.rows=deque();self.balance_rows=deque();self.states={};self.model=None
        self.definitions={key:value for key,value in RULES.items() if value[0]==action and (codes is None or key in codes)}
        self.last_stamp=None

    def reset(self):self.rows.clear();self.balance_rows.clear();self.states.clear();self.last_stamp=None

    def _pack(self,code,status,reason,stamp,evidence,message=None):
        definition=self.definitions[code];identity=(status,reason,message,evidence.get('side') if status=='issue' else None)
        prior=self.states.get(code)
        if prior is None or prior[0]!=identity:prior=(identity,stamp);self.states[code]=prior
        return {'code':code,'status':status,'reason':reason,'start_ms':prior[1],'end_ms':stamp,
            'rule_version':self.version,'evidence_level':'reference','body_parts':list(definition[2]),
            'message':message or definition[1] if status=='issue' else '本项未见持续偏差' if status=='not_observed' else '本项缺少连续可见证据',
            'suggestion':definition[5] if status=='issue' else '' if status=='not_observed' else '让相关部位连续入镜，再检查本项。',
            'evidence':{**evidence,'parameter_source':'pretrained-development-reference-not-independently-calibrated'}}

    def update(self,frame,context=None):
        stamp=frame['captured_ms'];data=frame.get('pretrained')
        if self.last_stamp is not None:
            gap=stamp-self.last_stamp
            if gap<=0 or gap>500:self.reset()
            elif gap>250:self.rows.clear()
        self.last_stamp=stamp
        if frame.get('identity_accepted') is False or frame.get('framing',{}).get('person_count',1)!=1:
            self.rows.clear();self.balance_rows.clear()
            return [self._pack(code,'unassessable','required_parts_or_identity_missing',stamp,{}) for code in self.definitions]
        source_stamp=frame.get('source_captured_ms',stamp)
        if not data or data.get('status')!='observed' or data.get('timestamp_ms')!=source_stamp:
            self.rows.clear();self.balance_rows.clear()
            return [self._pack(code,'unassessable','pretrained_input_unavailable',stamp,{}) for code in self.definitions]
        if self.action=='lat_pulldown':
            points=data.get('body',{}).get('keypoints',[])
            required=(5,6,9,10,11,12)
            visible=len(points)==17 and all(points[i].get('coordinate_valid') and points[i].get('score',0)>=.5 for i in required)
            engaged=visible and all(points[wrist]['y']<=points[shoulder]['y']+.25*(points[hip]['y']-points[shoulder]['y'])
                                   and points[hip]['y']-points[shoulder]['y']>=.08
                                   for shoulder,wrist,hip in ((5,9,11),(6,10,12)))
            if not engaged:
                self.rows.clear();self.balance_rows.clear()
                reason='exercise_not_engaged' if visible else 'pretrained_parts_not_visible'
                return [self._pack(code,'unassessable',reason,stamp,{}) for code in self.definitions]
        current={'stamp':stamp,'data':data};self.rows.append(current)
        while self.rows and stamp-self.rows[0]['stamp']>1400:self.rows.popleft()
        self.balance_rows.append(current)
        while self.balance_rows and stamp-self.balance_rows[0]['stamp']>2000:self.balance_rows.popleft()
        items=[]
        for code in self.definitions:
            evidence={'body_model_sha256':data.get('body_model_sha256'),'hand_model_sha256':data.get('hand_model_sha256'),
                'body_execution_backend':data.get('body',{}).get('execution_backend'),
                'hand_execution_backend':data.get('hands',[{}])[0].get('execution_backend') if data.get('hands') else None}
            requested=(context or {}).get('coaching_view','auto')
            if requested!='auto' and requested not in self.definitions[code][3]:
                items.append(self._pack(code,'unassessable','unsupported_view',stamp,evidence));continue
            if code.endswith('upper_body_balance'):
                feature=data.get('body_features',{}).get('scene_lean',{});value=feature.get('value')
                if feature.get('status')!='observed':
                    items.append(self._pack(code,'unassessable',feature.get('reason','scene_reference_unavailable'),stamp,evidence));continue
                intervals=[b['stamp']-a['stamp'] for a,b in zip(self.balance_rows,list(self.balance_rows)[1:])]
                cadence=median(intervals[-6:]) if intervals else 0
                window_ms=max(1100,max(3,math.ceil(900/cadence))*cadence+max(50,.2*cadence)) if cadence else 1100
                window=[r for r in self.balance_rows if stamp-r['stamp']<=min(1800,window_ms)]
                known=[r['data'].get('body_features',{}).get('scene_lean',{}) for r in window]
                known=[f for f in known if f.get('status')=='observed']
                matching=[f for f in known if f.get('side')==feature.get('side')]
                duration=stamp-window[0]['stamp']
                # 低帧率时仍须至少四次真实观测及足够持续时间，不补造中间帧。
                ready=len(window)>=4 and duration>=900
                prior=self.states.get(code);held=bool(prior and prior[0][0]=='issue' and prior[0][3]==feature.get('side'))
                boundary=4. if held else 6.;paths=[]
                for name,key in (('shoulder_and_trunk','shoulder_degrees'),('cross_model_trunk','source_trunk_degrees')):
                    pairs=[f for f in matching if type(f.get(key)) in (int,float) and f.get('trunk_degrees',0)*f[key]>0 and
                           (name!='cross_model_trunk' or abs(f['trunk_degrees']-f[key])<=4.)]
                    trunk=median([f['trunk_degrees'] for f in pairs]) if pairs else 0.
                    other=median([f[key] for f in pairs]) if pairs else 0.;share=len(pairs)/len(window)
                    current_angle=feature.get(key);coherent=(type(current_angle) in (int,float) and feature.get('trunk_degrees',0)*current_angle>0 and
                        min(abs(feature.get('trunk_degrees',0)),abs(current_angle))>=2. and
                        (name!='cross_model_trunk' or abs(feature['trunk_degrees']-current_angle)<=4.))
                    minimum=boundary if name=='cross_model_trunk' else 2. if held else 3.
                    bad=trunk*other>0 and min(abs(trunk),abs(other))>=minimum and abs((trunk+other)/2)>=boundary
                    paths.append({'name':name,'trunk':trunk,'other':other,'share':share,'confirmed':ready and share>=.8 and bad and coherent})
                selected=max(paths,key=lambda p:(p['confirmed'],p['share']));share=selected['share']
                status=('issue' if selected['confirmed'] else
                        'unassessable' if not ready or len(known)/len(window)<.8 or feature.get('candidate') else 'not_observed')
                evidence.update(feature)
                evidence.update(window_duration_ms=duration,direction_support_fraction=share,threshold=6.,recovery_threshold=4.,
                    confirmation_path=selected['name'],window_trunk_degrees=selected['trunk'],window_corroborating_degrees=selected['other'],
                    window_value_degrees=abs((selected['trunk']+selected['other'])/2))
                item=self._pack(code,status,'posture_duration_unconfirmed' if status=='unassessable' else '',stamp,evidence)
                if status=='issue':
                    item['side']=feature.get('side','none')
                    item['message']='身体向'+('左' if item['side']=='left' else '右')+'侧持续倾斜'
                items.append(item)
            elif code.endswith('finger_extension'):
                if data.get('body_features',{}).get('status')!='observed':
                    items.append(self._pack(code,'unassessable',data.get('body_features',{}).get('reason','pretrained_parts_not_visible'),stamp,evidence));continue
                def observed(row):
                    if row['data'].get('body_features',{}).get('status')!='observed':return [],[]
                    hands=row['data'].get('hands',[])
                    visible=[h for h in hands if h.get('body_wrist_index') in (9,10) and h.get('observation_with_body_wrist',{}).get('assessed_fingers',0)>=2]
                    extended=[h for h in visible if h['observation_with_body_wrist']['extended_fingers']>=2]
                    return visible,extended
                visible,extended=observed(current)
                motions={}
                for row in self.rows:
                    for side,value in row['data'].get('body_features',{}).get('elbow_angles',{}).items():motions.setdefault(side,[]).append(value)
                confirmed=[]
                for hand in extended:
                    wrist=hand['body_wrist_index'];start=stamp;count=0
                    for row in reversed(self.rows):
                        if not any(h['body_wrist_index']==wrist for h in observed(row)[1]):break
                        start=row['stamp'];count+=1
                    values=motions.get('left' if wrist==9 else 'right',[])
                    moving=len(values)>=4 and max(values)-min(values)>=10
                    if count>=6 and stamp-start>=700 and moving:confirmed.append(wrist)
                if not visible:status='unassessable';reason='pretrained_fingers_occluded'
                elif confirmed:status='issue';reason=''
                elif extended:status='unassessable';reason='posture_duration_unconfirmed'
                elif len({h.get('body_wrist_index') for h in visible}&{9,10})<2:
                    status='unassessable';reason='pretrained_one_hand_occluded'
                else:status='not_observed';reason=''
                evidence.update(assessed_hands=len(visible),extended_hands=len(extended),confirmed_body_wrists=confirmed,
                                contact_with_handle_verified=False,thumb_contact_verified=False,
                                hand_detector_sha256=data.get('hand_detector_sha256'))
                items.append(self._pack(code,status,reason,stamp,evidence))
            elif code.endswith('pretrained_posture'):
                from .exercise_feedback import ExerciseFeedbackModel
                if self.model is None:self.model=ExerciseFeedbackModel(self.action)
                prediction=self.model.observe(data.get('source_landmarks',[]));current['prediction']=prediction
                if prediction['status']!='candidate':
                    items.append(self._pack(code,'unassessable',prediction.get('reason','pretrained_parts_not_visible'),stamp,evidence));continue
                label=prediction['label'];confidence=prediction['confidence'];evidence.update(prediction)
                # 同一偏差跨伸展/回落阶段持续时保留证据，类别仍记录上游原标签。
                category=label.rsplit('_',1)[0] if label.endswith(('_up','_down')) else label
                current['category']=category
                if confidence<.8:status='unassessable';reason='pretrained_class_uncertain'
                elif category in {'b_correct','d_correct','s_correct'}:status='not_observed';reason=''
                else:
                    start=stamp;count=0
                    for row in reversed(self.rows):
                        p=row.get('prediction',{})
                        if row.get('category')!=category or p.get('confidence',0)<.8:break
                        start=row['stamp'];count+=1
                    status='issue' if count>=8 and stamp-start>=900 else 'unassessable';reason='' if status=='issue' else 'posture_duration_unconfirmed'
                message=('握距可能过宽，请检查两手位置' if 'arms_spread' in label else '握距可能过窄，请检查两手位置' if 'arms_narrow' in label else
                         '腰背姿态需要检查' if 'arch' in label or 'spine' in label else '膝盖方向需要检查' if 'caved_in' in label else '站距需要检查' if 'feet_spread' in label else None)
                items.append(self._pack(code,status,reason,stamp,evidence,message))
        return items
