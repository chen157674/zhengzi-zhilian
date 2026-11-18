"""计数之外的姿态评估适配器：共享实时／视频输入、分项拒判、参考建议与有界证据。"""
from collections import deque
import math
from statistics import median

from .frame_pipeline import usable_point
from .posture_rules import RULES, CANDIDATE_RULES, PARAMETER_SOURCE, describe, policy_version, rules_for
from .quality_candidates import CandidateRule, SCHEMA, SPECS, evaluate_cycle
from .movement_quality import DynamicRangeRule, evaluate_dynamic, hip_offset
from .barbell_quality import DeadliftExtensionRule, evaluate_deadlift_extension, observe_press_alignment, evaluate_row_trunk
from .pretrained_policy import RULES as PRETRAINED_RULES,body_enabled,active_rules
from .movement_guidance_policy import RULES as GUIDANCE_RULES,enabled as guidance_enabled


def reference_phase_counter(action):
    """未开放计数动作只估计阶段，绝不把此处事件记成已验证次数。"""
    from .cycles import CycleConfig
    if action == 'bodyweight_glute_bridge':
        from .glute_bridge import GluteBridgeCounter, GluteBridgeConfig
        return GluteBridgeCounter(GluteBridgeConfig(CycleConfig(120.,1,10.,20.,5.,200,100,100,200,500,800,30000,8.),
            35.,155.,.3,.15,.3,.2,.1,3,'reference-phase-bridge-1'))
    if action == 'bent_knee_sit_up':
        from .sit_up import SitUpCounter, SitUpConfig
        return SitUpCounter(SitUpConfig(CycleConfig(20.,1,10.,30.,5.,200,100,100,200,500,800,30000,10.),
            30.,150.,.15,55.,.2,3,'reference-phase-sit-up-1'))
    if action == 'high_knees':
        from .high_knees import HighKneesCounter, HighKneesConfig
        return HighKneesCounter(HighKneesConfig(CycleConfig(-.35,1,.06,.12,.03,100,33,33,50,200,200,30000,.04),
            .04,.03,.3,.6,.25,.12,3,'reference-phase-high-knees-1'))
    if action == 'barbell_overhead_press':
        from .overhead_press import OverheadPressCounter, OverheadPressConfig
        return OverheadPressCounter(OverheadPressConfig(CycleConfig(90.,1,15.,30.,8.,200,100,100,200,500,800,30000,15.),
            140.,95.,130.,1.,.6,120.,.45,20.,.3,3,'reference-phase-press-1'))
    return None


def points(frame, indices):
    if any(type(frame.get(k)) not in (int,float) or not math.isfinite(frame[k]) or frame[k]<=0 for k in ('width','height')):
        return None
    values = frame.get('framing', {}).get('landmarks', [])
    if frame.get('framing', {}).get('person_count') != 1 or frame.get('identity_accepted') is False or len(values) != 33:
        return None
    if any(not usable_point(values[i]) or values[i].get('valid') is False for i in indices):
        return None
    return {i: (values[i]['x']*frame['width'], values[i]['y']*frame['height']) for i in indices}


def view_ratio(frame):
    p = points(frame, [11,12,23,24])
    if not p:
        return None
    scale = (math.dist(p[11],p[23])+math.dist(p[12],p[24]))/2
    if scale < 1e-6:
        return None
    return math.dist(p[11],p[12])/scale


def estimate_view(frame):
    ratio = view_ratio(frame)
    if ratio is None:return 'unknown'
    return 'side' if ratio <= .35 else 'front' if ratio >= .75 else 'oblique'


def resolve_view(frame, requested):
    estimated = estimate_view(frame)
    if requested == 'auto':
        return estimated, 'estimated'
    if (requested, estimated) in {('side','front'), ('front','side')}:
        return 'unknown', 'view_mismatch'
    return requested, 'user_setting'


class PostureEngine:
    def __init__(self, action, counter,include_candidates=False):
        self.action = action
        self.counting_available = counter is not None
        self.counter = counter or reference_phase_counter(action)
        self.version = policy_version()+('-candidate' if include_candidates else '')
        self.rules = rules_for(action,include_candidates=include_candidates)
        self.frames = deque(maxlen=1200)
        self.static = {}
        self.view_ratios = deque()
        self.last_seq = self.last_stamp = self.epoch = None
        self.retired_epochs = deque(maxlen=16)
        self.pretrained=None
        if body_enabled():
            from .pretrained_quality import PretrainedObserver
            self.pretrained=PretrainedObserver(action,self.version,codes=active_rules())
        self.guidance=None
        if guidance_enabled():
            from .movement_guidance import MovementGuidance
            self.guidance=MovementGuidance(action,self.version)

    def reset(self, reason):
        self.frames.clear()
        self.static.clear()
        self.view_ratios.clear()
        if self.pretrained is not None:self.pretrained.reset()
        if self.guidance is not None:self.guidance.reset()
        if self.counter:
            self.counter.reset(reason)

    def prime(self, frame, context):
        return self.counter.prime(frame, context) if self.counter and hasattr(self.counter, 'prime') else {'events': [], 'observation': {}}

    def begin(self):
        self.frames.clear()
        self.static.clear()
        self.view_ratios.clear()
        if self.pretrained is not None:self.pretrained.reset()
        if self.guidance is not None:self.guidance.reset()
        self.last_seq = self.last_stamp = None
        if self.counter and hasattr(self.counter, 'begin'):
            self.counter.begin()

    def _side(self, frames, event=None):
        if event and event.get('side') in {'left', 'right'}:
            return event['side']
        for side, offset in (('left',0), ('right',1)):
            if frames and all(points(f, [11+offset,23+offset,25+offset,27+offset]) for f in frames):
                return side
        return None

    def _pack(self, result, code, event, view, basis):
        definition=RULES[code] if code in RULES else CANDIDATE_RULES[code]
        status = result.get('status', 'unassessable')
        if status not in {'issue','not_observed'}:
            status = 'unassessable'
        reason = result.get('reason') or ('evidence_unavailable' if status == 'unassessable' else '')
        message, suggestion = describe(code, status, reason)
        evidence = {key: result[key] for key in ('feature_name','value','threshold','hip_image_degrees','knee_image_degrees','trunk_change_degrees',
                    'measurement_side','range_by_side','raw_range_by_side','extreme_windows','minimum_window_ms','measurement_basis',
                    'minimum_hip_degrees','minimum_knee_degrees','longest_joint_extension_ms','minimum_stage_ms',
                    'reference_degrees','signed_angle_degrees','observed_frames','observed_duration_ms','maximum_observation_gap_ms') if key in result}
        evidence.update(view=view, view_basis=basis, exercise_basis='user_selected', parameter_source=PARAMETER_SOURCE)
        if code=='deadlift.extension' and status=='issue':
            limited=[label for key,label in (('hip_image_degrees','髋'),('knee_image_degrees','膝')) if result.get(key,180)<RULES[code][4]]
            evidence['limited_joints']=limited
            message='顶端'+('、'.join(limited)+'仍有屈曲，未达到伸展参考带' if limited else '请在起身时保持髋膝同时伸展')
            suggestion='在可控制的范围内让髋和膝同时自然伸展、站直后再回落；避免后仰或强行锁膝。'
        return {'code': code, 'status': status, 'reason': reason,
                'start_ms': result.get('start_ms', event['start_ms']),
                'end_ms': result.get('end_ms', event['end_ms']),
                'rule_version': self.version, 'evidence_level': 'reference',
                'body_parts': list(definition[2]), 'message': message,
                'suggestion': suggestion, 'evidence': evidence}

    def _cycle(self, event, view, basis):
        frames = [f for f in self.frames if event['start_ms'] <= f['captured_ms'] <= event['end_ms']]
        side = self._side(frames, event)
        outputs = []
        for rule in self.rules:
            code = rule['code']
            if code in PRETRAINED_RULES or code in GUIDANCE_RULES:continue
            if self.action in {'forearm_plank','barbell_overhead_press'} or code.endswith(('trunk_sway','upper_arm_swing','arm_asymmetry','trunk_lateral_lean')):
                continue
            result = {'status':'unassessable','reason':'unsupported_view'}
            if view in rule['views'] and all(f.get('posture_view',view) in rule['views'] for f in frames):
                if code in {'push_up.hip_high','push_up.hip_low'}:
                    values=[hip_offset(f,side,(0,-1)) for f in frames]
                    if len(frames)<5 or any(v is None for v in values):
                        result={'status':'unassessable','reason':'required_parts_or_identity_missing'}
                    else:
                        start=None;longest=0
                        for f,value in zip(frames,values):
                            bad=value>rule['threshold'] if code.endswith('high') else value < -rule['threshold']
                            if bad:
                                if start is None:start=f['captured_ms']
                                longest=max(longest,f['captured_ms']-start)
                            else:start=None
                        result={'status':'issue' if longest>=80 else 'not_observed','reason':None,
                                'value':max(values) if code.endswith('high') else min(values),'threshold':rule['threshold'],
                                'feature_name':'hip_offset_from_shoulder_ankle_line','measurement_side':side,
                                'measurement_basis':'侧面肩踝连线的有向垂距／肩踝长度；偏差连续至少80ms'}
                elif self.action in SPECS:
                    spec = CandidateRule(code, SCHEMA, rule['threshold'], .02 if self.action=='bodyweight_glute_bridge' else 8.,
                                         80, 500, self.version, PARAMETER_SOURCE)
                    result = evaluate_cycle(self.action, event, list(self.frames), measurement_side=side,
                                            view=view, variant_confirmed=True, rule=spec)
                elif self.action == 'conventional_deadlift':
                    spec = DeadliftExtensionRule(160.,160.,8.,80,500,self.version,PARAMETER_SOURCE)
                    result = evaluate_deadlift_extension(event, list(self.frames), view=view,
                                conventional_barbell_confirmed=True, measurement_side=side, rule=spec)
                elif self.action == 'barbell_bent_over_row':
                    result = evaluate_row_trunk(event,frames,rule['threshold'])
                else:
                    reference = next((f for f in reversed(self.frames) if f['captured_ms']<=event['start_ms']), None)
                    band = 10. if code=='jumping_jack.arm_range' else .04
                    spec = DynamicRangeRule(code, rule['threshold'], band, 50, 250, self.version, PARAMETER_SOURCE)
                    result = evaluate_dynamic(event, list(self.frames), reference, view=view, variant_confirmed=True,
                                    orientation_up=(0,-1), orientation_confirmed=True, rule=spec)
            outputs.append(self._pack(result, code, event, view, basis))
        return outputs

    def _static(self, frame, view, basis, produced,context=None):
        from .gym_catalogue import BY_ID
        if self.action in BY_ID or self.action=='standing_dumbbell_curl':
            from .gym_quality import observe
            return observe(self,frame,view,basis,
                           codes={'curl.trunk_sway','curl.upper_arm_swing'} if self.action=='standing_dumbbell_curl' else None,
                           movement_pattern=(context or {}).get('movement_pattern'))
        stamp = frame['captured_ms']
        items, intervals = [], []
        if self.action not in {'forearm_plank','barbell_overhead_press'}:
            return items, intervals
        side = getattr(self.counter, 'measurement_side', None) or self._side([frame])
        for rule in self.rules:
            code = rule['code']
            if code in GUIDANCE_RULES:continue
            value, issue, reason = None, False, None
            affected_sides=[]
            arm_deviation={}
            if view not in rule['views']:
                reason = 'unsupported_view'
            elif self.action == 'forearm_plank':
                if not produced.get('intervals'):
                    reason = 'forearm_support_unconfirmed'
                else:
                    value = hip_offset(frame, side, (0,-1))
                    if value is None:
                        reason = 'required_parts_or_identity_missing'
                    else:
                        issue = value > rule['threshold'] if code.endswith('high') else value < -rule['threshold']
            else:
                p = points(frame,[11,12,13,14,15,16,23,24])
                rack = p is not None and all(p[15+s][1] < p[13+s][1] and
                    abs(p[15+s][1]-p[11+s][1]) < .65*math.dist(p[11+s],p[23+s]) for s in (0,1))
                observed = observe_press_alignment(frame, view=view, standing_barbell_confirmed=True,
                                                    rack_phase_confirmed=rack, orientation_up=(0,-1))
                if 'arms' not in observed:
                    reason = observed.get('reason','rack_phase_unconfirmed')
                else:
                    value = max(abs(a['elbow_outside_wrist_forearm_ratio']) for a in observed['arms'].values())
                    issue = value > rule['threshold']
                    affected_sides=[side for side,arm in observed['arms'].items() if abs(arm['elbow_outside_wrist_forearm_ratio'])>rule['threshold']]
                    arm_deviation={side:{'ratio':arm['elbow_outside_wrist_forearm_ratio'],
                                         'direction':'outward' if arm['elbow_outside_wrist_forearm_ratio']>0 else 'inward'}
                                   for side,arm in observed['arms'].items() if side in affected_sides}
            state = 'unassessable' if reason else 'issue' if issue else 'not_observed'
            previous = self.static.get(code)
            signature=tuple((side,data['direction']) for side,data in arm_deviation.items())
            if previous is None or previous['state'] != state or previous['reason'] != reason or previous['signature']!=signature:
                previous = self.static[code] = {'state':state,'reason':reason,'signature':signature,'start':stamp}
            confirmed = stamp-previous['start'] >= 700
            result = {'status':state if state!='issue' or confirmed else 'unassessable',
                      'reason':reason or (None if confirmed or state!='issue' else 'posture_duration_unconfirmed'),
                      'value':value,'threshold':rule['threshold']}
            item = self._pack(result,code,{'start_ms':previous['start'],'end_ms':stamp},view,basis)
            item['side']='none'
            item['evidence']['affected_sides']=affected_sides
            if self.action=='barbell_overhead_press':
                item['evidence'].update(arm_deviation=arm_deviation,scope='起始位可见肘腕关系；不验证全身站姿')
                if item['status']=='issue':
                    labels=[('左' if side=='left' else '右')+'肘相对手腕偏'+('外' if data['direction']=='outward' else '内') for side,data in arm_deviation.items()]
                    item['message']='起始位'+ '、'.join(labels)
            if value is not None:
                previous['minimum']=min(previous.get('minimum',value),value)
                previous['maximum']=max(previous.get('maximum',value),value)
                item['evidence'].update(value_min=previous['minimum'],value_max=previous['maximum'])
            item['evidence']['observed_duration_ms']=stamp-previous['start']
            items.append(item)
            if stamp>previous['start']:
                intervals.append(item)
        return items, intervals

    def process(self, frame, context):
        stamp, seq = frame.get('captured_ms'), frame.get('frame_seq')
        if type(stamp) is not int or type(seq) is not int or min(stamp,seq)<0:
            self.reset('invalid_frame_identity')
            return {'events':[],'observation':{'posture':{'items':[],'reason':'invalid_frame_identity'}}}
        epoch = context.get('stream_epoch','source')
        if epoch in self.retired_epochs or frame.get('stream_epoch',epoch)!=epoch:
            return {'events':[],'observation':{'posture':{'items':[],'reason':'stale_stream'}}}
        if self.epoch is not None and self.epoch != epoch:
            self.retired_epochs.append(self.epoch)
            self.reset('new_stream')
            self.last_seq = self.last_stamp = None
        self.epoch = epoch
        if self.last_seq is not None and (seq<=self.last_seq or stamp<=self.last_stamp):
            return {'events':[],'observation':{'posture':{'items':[],'reason':'duplicate_or_old_frame'}}}
        if self.last_stamp is not None and stamp-self.last_stamp>500:
            self.reset('source_gap')
        self.last_seq, self.last_stamp = seq, stamp
        requested=context.get('coaching_view','auto')
        view, basis = resolve_view(frame, requested)
        if requested=='auto':
            ratio=view_ratio(frame)
            if ratio is None:
                self.view_ratios.clear()
            else:
                if self.view_ratios and abs(ratio-median(value for _,value in self.view_ratios))>.3:self.view_ratios.clear()
                self.view_ratios.append((stamp,ratio))
                while self.view_ratios and stamp-self.view_ratios[0][0]>600:self.view_ratios.popleft()
                stable=median(value for _,value in self.view_ratios)
                view='side' if stable<=.35 else 'front' if stable>=.75 else 'oblique'
        frame={**frame,'posture_view':view}
        self.frames.append(frame)
        while self.frames and stamp-self.frames[0]['captured_ms']>31000:
            self.frames.popleft()
        produced = self.counter.process(frame, context) if self.counter else {'events':[], 'observation':{}}
        items, extra_intervals = self._static(frame,view,basis,produced,context)
        for event in produced.get('events', []):
            assessed = self._cycle(event,view,basis)
            items.extend(assessed)
            if self.counting_available:
                event['assessments'] = assessed
            else:
                for item in assessed:
                    extra_intervals.append({**item,'side':event['side']})
        if not self.counting_available:
            produced['events'] = []
        if self.pretrained is not None:
            extra=self.pretrained.update(frame,context);items.extend(extra)
            extra_intervals.extend({**item,'side':item.get('side','none')} for item in extra if item['end_ms']>item['start_ms'])
        if self.guidance is not None:
            extra,findings=self.guidance.update(frame,context);items.extend(extra);extra_intervals.extend(findings)
        if extra_intervals:
            produced['posture_findings'] = extra_intervals
        produced.setdefault('observation',{})['posture'] = {
            'schema_version':1, 'rule_version':self.version,'evidence_level':'reference',
            'view':view,'view_basis':basis,'items':items,
            'reason':None if items else 'awaiting_complete_cycle',
            'muscle_force_assessment':False}
        return produced


def create_analysis_engine(action,*,include_candidates=False):
    if not rules_for(action,include_candidates=include_candidates):
        return None
    from .counting_resources import create_counter
    return PostureEngine(action, create_counter(action),include_candidates=include_candidates)
