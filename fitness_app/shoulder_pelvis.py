"""双模型可见肩髋相对方向候选；不测重力，不接生产。"""
from collections import deque
import math
from statistics import median
import numpy as np
CONFIG={'primary_minimum_degrees':8.,'secondary_minimum_degrees':4.,'maximum_disagreement_degrees':8.,
        'minimum_duration_ms':900,'minimum_observations':4,'minimum_support_fraction':.8,
        'maximum_gap_ms':500,'window_ms':1800,'minimum_view_ratio':.55,'runtime_enabled':False}


def geometry(points,indices,width,height,primary):
    if not isinstance(points,list) or len(points)<max(indices)+1:return None
    selected=[points[i] for i in indices]
    for point in selected:
        if not isinstance(point,dict) or point.get('valid') is False:return None
        if primary:
            if not point.get('coordinate_valid') or point.get('score',0)<.5:return None
        elif min(point.get('visibility',0),point.get('presence',point.get('visibility',0)))<.5:return None
        if any(type(point.get(k)) not in (int,float) or not math.isfinite(point[k]) or not 0<=point[k]<=1 for k in ('x','y')):return None
    xy=np.asarray([[p['x']*width,p['y']*height] for p in selected],np.float64)
    shoulder=xy[1]-xy[0];pelvis=xy[3]-xy[2];trunk=xy[:2].mean(0)-xy[2:].mean(0)
    if np.linalg.norm(trunk)<.08*height or min(np.linalg.norm(shoulder),np.linalg.norm(pelvis))<.04*width:return None
    if np.linalg.norm(shoulder)/np.linalg.norm(trunk)<CONFIG['minimum_view_ratio']:return None
    def line(vector):return math.degrees(math.atan2(float(vector[1]),float(vector[0])))
    relative=(line(shoulder)-line(pelvis)+90)%180-90
    return {'signed_degrees':relative,'view_ratio':float(np.linalg.norm(shoulder)/np.linalg.norm(trunk))}


def measure(data,width,height):
    if data.get('status')!='observed':return {'status':'unassessable','reason':'input_unavailable'}
    primary=geometry(data.get('body',{}).get('keypoints',[]),(5,6,11,12),width,height,True)
    secondary=geometry(data.get('source_landmarks',[]),(11,12,23,24),width,height,False)
    if primary is None or secondary is None:return {'status':'unassessable','reason':'required_parts_or_view_unavailable'}
    first,second=primary['signed_degrees'],secondary['signed_degrees']
    agreed=first*second>0 and abs(first-second)<=CONFIG['maximum_disagreement_degrees']
    candidate=agreed and abs(first)>=CONFIG['primary_minimum_degrees'] and abs(second)>=CONFIG['secondary_minimum_degrees']
    return {'status':'observed','primary_signed_degrees':first,'secondary_signed_degrees':second,
        'models_agree':agreed,'candidate':bool(candidate),'direction':1 if first>0 else -1,
        'measurement_basis':'same_frame_shoulder_line_relative_to_pelvis_not_gravity_or_spine_angle'}


class ShoulderPelvisCandidate:
    def __init__(self):self.rows=deque();self.last=None
    def reset(self):self.rows.clear();self.last=None
    def update(self,frame):
        stamp=frame['captured_ms'];data=frame.get('pretrained') or {}
        if self.last is not None and (stamp<=self.last or stamp-self.last>CONFIG['maximum_gap_ms']):self.reset()
        self.last=stamp
        if frame.get('identity_accepted') is False or frame.get('framing',{}).get('person_count')!=1 or data.get('timestamp_ms')!=frame.get('source_captured_ms',stamp):
            self.rows.clear();return {'status':'unassessable','reason':'identity_or_source_unavailable'}
        points=data.get('body',{}).get('keypoints',[])
        engaged=len(points)==17 and all(points[i].get('coordinate_valid') and points[i].get('score',0)>=.5 for i in (5,6,9,10,11,12))
        engaged=engaged and all(points[w]['y']<=points[s]['y']+.25*(points[h]['y']-points[s]['y']) and points[h]['y']-points[s]['y']>=.08 for s,w,h in ((5,9,11),(6,10,12)))
        if not engaged:self.rows.clear();return {'status':'unassessable','reason':'exercise_not_engaged'}
        value=measure(data,frame['width'],frame['height'])
        if value['status']!='observed':self.rows.clear();return value
        self.rows.append((stamp,value))
        while self.rows and stamp-self.rows[0][0]>CONFIG['window_ms']:self.rows.popleft()
        duration=stamp-self.rows[0][0]
        matching=[v for t,v in self.rows if v['candidate'] and v['direction']==value['direction']]
        share=len(matching)/len(self.rows)
        ready=len(self.rows)>=CONFIG['minimum_observations'] and duration>=CONFIG['minimum_duration_ms']
        confirmed=ready and value['candidate'] and share>=CONFIG['minimum_support_fraction']
        return {**value,'status':'issue' if confirmed else 'unassessable' if not ready or value['candidate'] else 'not_observed',
            'window_duration_ms':duration,'observed_frames':len(self.rows),'support_fraction':share,
            'primary_window_degrees':median([v['primary_signed_degrees'] for v in matching]) if matching else None,
            'secondary_window_degrees':median([v['secondary_signed_degrees'] for v in matching]) if matching else None,
            'runtime_enabled':False}
