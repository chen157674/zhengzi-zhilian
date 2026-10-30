"""组内可比的二维运动摘要；仅保留标量和区间统计，不保存关节轨迹。"""
import hashlib,math
from collections import deque
from statistics import median
from .frame_pipeline import usable_point
from .gym_catalogue import BY_ID

VERSION='movement-stability-1'
WINDOW_MS=3000
MAX_GAP_MS=500
MIN_COVERAGE=.8
MIN_HALF_SAMPLES=3
MAX_SCALE_RATIO=1.25
MAX_WINDOWS=1000
CYCLE_METRICS={
 'bodyweight_squat':('knee',),'standard_push_up':('elbow',),'reverse_lunge':('knee',),
 'bodyweight_glute_bridge':('hip',),'bent_knee_sit_up':('hip',),'high_knees':('hip',),
 'standing_dumbbell_curl':('elbow',),'jumping_jack':('arm',),'conventional_deadlift':('hip','knee'),
 'barbell_bent_over_row':('trunk',)}
CYCLE_VIEWS={'bodyweight_squat':{'side'},'standard_push_up':{'side'},'reverse_lunge':{'oblique'},
 'bodyweight_glute_bridge':{'side'},'bent_knee_sit_up':{'side'},'high_knees':{'front','oblique'},
 'standing_dumbbell_curl':{'side','oblique'},'jumping_jack':{'front'},'conventional_deadlift':{'side'},'barbell_bent_over_row':{'side','oblique'}}
BILATERAL={'bench','incline','lying_fly','overhead','raise','y_raise','chest_press','fly','reverse_fly','bent_fly','face_pull','pull_up','pulldown','row'}
TITLES={'knee_range':'膝部图像活动范围','hip_range':'髋部图像活动范围','elbow_range':'肘部图像活动范围',
 'arm_range':'上臂图像活动范围','trunk_range':'躯干图像方向变化范围','wrist_difference':'左右腕图像高度差',
 'hip_line_deviation':'髋与肩踝连线的图像偏离'}


def quantile(values,q):
    values=sorted(values);index=(len(values)-1)*q;left=int(index);right=min(left+1,len(values)-1)
    return values[left]*(right-index)+values[right]*(index-left) if right!=left else values[left]


def distribution(values):
    center=median(values)
    return {'median':center,'mad':median(abs(v-center) for v in values),'p10':quantile(values,.1),'p90':quantile(values,.9),'minimum':min(values),'maximum':max(values)}


def _angle(a,b,c):
    x=(a[0]-b[0],a[1]-b[1]);y=(c[0]-b[0],c[1]-b[1]);scale=math.hypot(*x)*math.hypot(*y)
    if scale<1e-6:return None
    return math.degrees(math.acos(max(-1.,min(1.,(x[0]*y[0]+x[1]*y[1])/scale))))


def scalars(frame,view,basis,rule_version=None):
    stamp=frame.get('captured_ms');width=frame.get('width');height=frame.get('height');q=frame.get('framing',{});p=q.get('landmarks',[])
    row={'t':stamp,'view':view or 'unknown','basis':basis or 'unknown','model':frame.get('model_version'),'rule_version':rule_version,'aspect':width/height if type(width) in (int,float) and type(height) in (int,float) and height>0 else None,'sides':{},'bilateral':None}
    if q.get('person_count')!=1 or frame.get('identity_accepted') is False or q.get('code')=='TARGET_AMBIGUOUS' or len(p)!=33 or row['aspect'] is None:return row
    def point(i):
        item=p[i]
        if item.get('valid') is not True or not usable_point(item) or min(item.get('visibility',0),item.get('presence',0))<.7:return None
        if not 0<=item['x']<=1 or not 0<=item['y']<=1:return None
        return item['x']*width,item['y']*height
    for side,shift in [('left',0),('right',1)]:
        points=[point(i+shift) for i in (11,13,15,23,25,27)];shoulder,elbow,wrist,hip,knee,ankle=points
        if shoulder is None or hip is None:continue
        length=math.dist(shoulder,hip)
        if length<1e-5:continue
        values={'scale':length/math.hypot(width,height),'trunk':math.degrees(math.atan2(abs(shoulder[0]-hip[0]),hip[1]-shoulder[1]))}
        for name,parts in [('elbow',(shoulder,elbow,wrist)),('arm',(elbow,shoulder,hip)),('hip',(shoulder,hip,knee)),('knee',(hip,knee,ankle))]:
            if all(v is not None for v in parts):
                value=_angle(*parts)
                if value is not None:values[name]=value
        if ankle is not None and math.dist(shoulder,ankle)>1e-5:
            dx,dy=ankle[0]-shoulder[0],ankle[1]-shoulder[1]
            values['hip_line_deviation']=abs(dx*(hip[1]-shoulder[1])-dy*(hip[0]-shoulder[0]))/(dx*dx+dy*dy)
        if wrist is not None:values['wrist_y']=wrist[1]
        row['sides'][side]=values
    if len(row['sides'])==2 and all('wrist_y' in values for values in row['sides'].values()):
        a,b=row['sides'].values();row['bilateral']={'scale':(a['scale']+b['scale'])/2,'wrist_difference':abs(a['wrist_y']-b['wrist_y'])/((a['scale']+b['scale'])/2*math.hypot(width,height))}
    return row


class Collector:
    def __init__(self,action,offset=0):
        self.action=action;self.offset=offset;self.rows=deque();self.last=-1;self.group=None;self.epoch=None;self.start=None;self.seen=set();self.seen_order=deque()
    def push(self,frame,output,group=1,epoch='single'):
        stamp=frame.get('captured_ms');posture=output.get('observation',{}).get('posture',{})
        if type(stamp) is not int or stamp<self.offset or stamp<self.last:return []
        result=[]
        if (group,epoch)!=(self.group,self.epoch):
            result.extend(self.finish());self.rows.clear();self.start=stamp;self.group=group;self.epoch=epoch
        row=scalars(frame,posture.get('view'),posture.get('view_basis'),posture.get('rule_version'))
        row['evaluable']=any(v.get('status') in {'issue','not_observed'} for v in posture.get('items',[]))
        if not self.rows or stamp-self.rows[-1]['t']>=50:self.rows.append(row)
        while self.rows and stamp-self.rows[0]['t']>31000:self.rows.popleft()
        self.last=max(self.last,stamp)
        if self.action in CYCLE_METRICS:
            cycles=[(e['start_ms'],e['end_ms'],e.get('side','none'),'cycle') for e in output.get('events',[])]
            if not cycles:
                cycles=[(i['start_ms'],i['end_ms'],i.get('side','none'),'reference_interval') for i in output.get('posture_findings',[]) if i.get('status') in {'issue','not_observed','unassessable'} and i['end_ms']>i['start_ms']]
            for start,end,side,kind in cycles:
                key=(group,epoch,start,end,side)
                if key in self.seen:continue
                self.seen.add(key);self.seen_order.append(key)
                if len(self.seen_order)>2000:self.seen.discard(self.seen_order.popleft())
                rows=[v for v in self.rows if start<=v['t']<=end]
                result.append(self._window(rows,start,end,kind,side))
        else:
            while self.start is not None and stamp>=self.start+WINDOW_MS:
                end=self.start+WINDOW_MS;rows=[v for v in self.rows if self.start<=v['t']<end]
                result.append(self._window(rows,self.start,end,'window','none'));self.start=end
        return result
    def _window(self,rows,start,end,kind,side):
        views={v['view'] for v in rows if v['view']!='unknown'};bases={v['basis'] for v in rows if v['view']!='unknown'}
        view=next(iter(views)) if len(views)==1 else 'mixed' if views else 'unknown'
        basis=next(iter(bases)) if len(bases)==1 else 'mixed' if bases else 'unknown'
        result={'start_ms':max(0,start-self.offset),'end_ms':max(0,end-self.offset),'group':self.group,
            'continuity':hashlib.sha256(str(self.epoch).encode()).hexdigest()[:16],'kind':kind,'view':view,'view_basis':basis,'metrics':[],
            'aspect_ratio':median(v['aspect'] for v in rows if v['aspect'] is not None) if any(v['aspect'] is not None for v in rows) else None,
            'pose_models':sorted({v['model'] for v in rows if isinstance(v['model'],str)}),
            'posture_rule_versions':sorted({v['rule_version'] for v in rows if isinstance(v['rule_version'],str)})}
        if self.action in CYCLE_METRICS:specs=[(name,name+'_range',s) for name in CYCLE_METRICS[self.action] for s in ([side] if side in ('left','right') else ['left','right'])]
        elif self.action=='forearm_plank':specs=[('hip_line_deviation','hip_line_deviation',s) for s in ('left','right')]
        else:
            specs=[('trunk','trunk_range',s) for s in ('left','right')]
            family=BY_ID.get(self.action,{}).get('family')
            if family in BILATERAL or self.action=='barbell_overhead_press':specs.append(('wrist_difference','wrist_difference','both'))
        for key,metric,measurement_side in specs:
            output={'metric':metric,'title':TITLES[metric],'side':measurement_side,'status':'unassessable','reason':'coverage_insufficient','coverage':0.,'value':None,'scale':None,
                'unit':'torso_ratio' if metric in {'wrist_difference','hip_line_deviation'} else 'degrees','statistics':None}
            pairs=[];covered=0.;previous=None
            for row in rows:
                values=row['bilateral'] if measurement_side=='both' else row['sides'].get(measurement_side)
                valid=values is not None and key in values and math.isfinite(values[key]) and row['view']==view and row['basis']==basis
                if self.action=='forearm_plank' and not row['evaluable']:valid=False
                if valid:
                    pairs.append((row,values))
                    if previous is not None and 0<row['t']-previous<=MAX_GAP_MS:covered+=row['t']-previous
                    previous=row['t']
                else:previous=None
            duration=end-start;output['coverage']=min(1.,covered/duration) if duration>0 else 0.
            if view not in {'front','side','oblique'} or basis in {'mixed','view_mismatch','unknown'}:output['reason']='view_unconfirmed'
            elif self.action in CYCLE_VIEWS and view not in CYCLE_VIEWS[self.action] or self.action=='forearm_plank' and view!='side':output['reason']='unsupported_view'
            elif metric=='wrist_difference' and view!='front':output['reason']='unsupported_view'
            elif len(pairs)>=8 and output['coverage']>=MIN_COVERAGE:
                scales=[values['scale'] for _,values in pairs];aspects=[row['aspect'] for row,_ in pairs]
                if min(scales)<=0 or quantile(scales,.9)/quantile(scales,.1)>MAX_SCALE_RATIO or max(aspects)-min(aspects)>.02:output['reason']='scale_or_aspect_changed'
                else:
                    moving=kind!='window' or self.action=='forearm_plank' and any(row['evaluable'] for row in rows)
                    if not moving:
                        for s in ('left','right'):
                            for name in ('arm','elbow'):
                                values=[r['sides'][s][name] for r in rows if s in r['sides'] and name in r['sides'][s]]
                                if len(values)>=8 and quantile(values,.9)-quantile(values,.1)>=8:moving=True
                    if moving:
                        values=[v[key] for _,v in pairs];stats=distribution(values)
                        output.update(status='observed',reason=None,scale=median(scales),statistics=stats,
                            value=stats['p90']-stats['p10'] if metric.endswith('_range') else stats['median'])
                    else:output['reason']='movement_unconfirmed'
            result['metrics'].append(output)
        return result
    def finish(self):
        if self.action not in CYCLE_METRICS and self.start is not None and self.last>self.start:
            start=self.start;self.start=self.last
            return [self._window([v for v in self.rows if start<=v['t']<=self.last],start,self.last,'partial_window','none')]
        return []


def report(windows):
    groups={};results=[]
    for window in windows:groups.setdefault((window['group'],window['continuity']),[]).append(window)
    for (group,continuity),rows in groups.items():
        rows=sorted(rows,key=lambda v:v['start_ms']);views={v['view'] for v in rows if v['view'] not in {'unknown','mixed'}};bases={v['view_basis'] for v in rows if v['view_basis'] not in {'unknown','mixed'}}
        aspects=[r['aspect_ratio'] for r in rows if r.get('aspect_ratio') is not None];models={v for r in rows for v in r.get('pose_models',[])};rules={v for r in rows for v in r.get('posture_rule_versions',[])}
        keys={(m['metric'],m['side'],m['unit'],row['kind']) for row in rows for m in row['metrics'] if row['kind']!='partial_window'}
        for metric,side,unit,kind in sorted(keys):
            cohort=[(r,next(m for m in r['metrics'] if m['metric']==metric and m['side']==side)) for r in rows if r['kind']==kind and any(m['metric']==metric and m['side']==side for m in r['metrics'])]
            if not cohort:continue
            middle=(cohort[0][0]['start_ms']+cohort[-1][0]['end_ms'])/2
            halves=[[v for v in cohort if ((v[0]['start_ms']+v[0]['end_ms'])/2<=middle)==early] for early in (True,False)]
            out={'group':group,'continuity':continuity,'metric':metric,'title':TITLES[metric],'side':side,'unit':unit,'kind':kind,'view':next(iter(views)) if len(views)==1 else 'mixed',
                'state':'insufficient_evidence','message':'前后各需至少三个可比观察，未知不计为稳定。','before':None,'after':None,'direction':None,'delta':None}
            out['excluded_partial_ms']=sum(r['end_ms']-r['start_ms'] for r in rows if r['kind']=='partial_window')
            if len(views)>1 or len(bases)>1 or len(models)>1 or len(rules)>1 or aspects and max(aspects)-min(aspects)>.02 or any(r['view']=='mixed' or r['view_basis']=='mixed' for r in rows):out.update(state='not_comparable',message='取景或测量条件发生变化，未比较前后差异。')
            elif any(m['reason']=='scale_or_aspect_changed' for _,m in cohort):out.update(state='not_comparable',message='观察窗内尺度或显示比例变化较大，未比较前后差异。')
            else:
                eligible=[[m for _,m in half if m and m['status']=='observed'] for half in halves]
                if all(len(v)>=MIN_HALF_SAMPLES for v in eligible) and all(len(v)/len(h)>=MIN_COVERAGE for v,h in zip(eligible,halves) if h):
                    scales=[median(m['scale'] for m in v) for v in eligible]
                    if min(scales)<=0 or max(scales)/min(scales)>MAX_SCALE_RATIO:out.update(state='not_comparable',message='前后身体投影尺度变化较大，未比较。')
                    else:
                        before,after=[distribution([m['value'] for m in v]) for v in eligible];delta=after['median']-before['median'];threshold=max(.03 if unit=='torso_ratio' else 3.,before['mad'],after['mad'])
                        direction='increase' if delta>threshold else 'decrease' if delta< -threshold else 'no_clear_change'
                        descriptions={'increase':'后半段'+TITLES[metric]+'增大。','decrease':'后半段'+TITLES[metric]+'减小。','no_clear_change':'这一项前后未观察到超过工程噪声带的明确变化。'}
                        out.update(state='compared',message=descriptions[direction],direction=direction,delta=delta,change_threshold=threshold,
                            before={**before,'samples':len(eligible[0]),'unknown':len(halves[0])-len(eligible[0]),'start_ms':halves[0][0][0]['start_ms'],'end_ms':halves[0][-1][0]['end_ms']},
                            after={**after,'samples':len(eligible[1]),'unknown':len(halves[1])-len(eligible[1]),'start_ms':halves[1][0][0]['start_ms'],'end_ms':halves[1][-1][0]['end_ms']})
                        for name,items in zip(('before','after'),eligible):
                            out[name]['raw_observed_minimum']=min(m['statistics']['minimum'] for m in items)
                            out[name]['raw_observed_maximum']=max(m['statistics']['maximum'] for m in items)
            results.append(out)
    return results
