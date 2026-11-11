"""新增动作的连续可见关节证据；骨架只确认运动族，不猜器械。"""
import math
import numpy as np
from .upstream import calculate_angle
from .frame_pipeline import usable_point
from .gym_catalogue import BY_ID


def measurements(frames,require_wrist=True,*,minimum_frames=8):
    if len(frames)<minimum_frames or frames[-1]['captured_ms']-frames[0]['captured_ms']<1200:return None
    times=np.array([f['captured_ms'] for f in frames])
    if np.any(np.diff(times)<=0) or np.max(np.diff(times))>500:return None
    series={side:[] for side in ('left','right')};bilateral=[]
    for frame in frames:
        q=frame.get('framing',{});p=q.get('landmarks',[])
        if q.get('person_count')!=1 or q.get('code')=='TARGET_AMBIGUOUS' or frame.get('identity_accepted') is False:return None
        w,h=frame.get('width'),frame.get('height')
        if len(p)!=33 or any(type(v) not in (float,int) or not math.isfinite(v) or v<=0 for v in (w,h)):return None
        def pt(i):
            a=p[i]
            return np.array((a['x']*w,a['y']*h)) if usable_point(a) and a.get('valid') is not False else None
        valid={}
        for side,s in (('left',0),('right',1)):
            shoulder,elbow,wrist,hip=[pt(i+s) for i in (11,13,15,23)]
            if any(v is None for v in (shoulder,elbow,hip)) or require_wrist and wrist is None:continue
            scale=np.linalg.norm(shoulder-hip)
            if scale<1e-5:continue
            try:
                row={'stamp':frame['captured_ms'],'elbow':calculate_angle(shoulder,elbow,wrist) if wrist is not None else float('nan'),
                     'arm':calculate_angle(elbow,shoulder,hip),
                     'trunk':math.degrees(math.atan2(hip[1]-shoulder[1],abs(hip[0]-shoulder[0]))),
                     'wrist_up':float((shoulder[1]-wrist[1])/scale) if wrist is not None else float('nan'),
                     'reach':float(np.linalg.norm(wrist-shoulder)/scale) if wrist is not None else float('nan'),
                     'wrist_x':float((wrist[0]-shoulder[0])/scale) if wrist is not None else float('nan')}
            except ValueError:continue
            series[side].append(row);valid[side]=(shoulder,elbow,wrist,hip,scale)
        if len(valid)==2 and all(v[2] is not None for v in valid.values()):
            l,r=valid['left'],valid['right'];scale=(l[4]+r[4])/2
            # 以各自肩为基准消除轻微肩线斜度；镜像不改变高度差。
            bilateral.append({'stamp':frame['captured_ms'],'height_difference':float(abs((l[2][1]-l[0][1])-(r[2][1]-r[0][1]))/scale)})
    def sufficient(rows):
        if len(rows)<minimum_frames or len(rows)/len(frames)<.8:return False
        stamps=np.array([r['stamp'] for r in rows])
        return max(stamps[0]-times[0],times[-1]-stamps[-1],np.max(np.diff(stamps)))<=500
    series={side:rows for side,rows in series.items() if sufficient(rows)}
    if not series:return None
    return {'sides':series,'bilateral':bilateral if sufficient(bilateral) else [],'start_ms':int(times[0]),'end_ms':int(times[-1])}


def quantile(rows,key,p=50):
    values=[r[key] for r in rows if math.isfinite(r[key])]
    return float(np.percentile(values,p)) if values else float('nan')
def span(rows,key):return quantile(rows,key,90)-quantile(rows,key,10)


def motion_gate(action,values):
    if not values:return False
    family=BY_ID[action]['family']
    for rows in values['sides'].values():
        t=quantile(rows,'trunk');e=span(rows,'elbow');a=span(rows,'arm');up=quantile(rows,'wrist_up',90)
        moving=e>16 or a>16 or span(rows,'reach')>.18
        if not moving:continue
        if family in {'bench','lying_fly'} and abs(t)<35 and up>.15:return True
        if family=='incline' and 20<t<75 and up>.15 and e>18:return True
        if family=='bent_fly' and 5<t<65 and a>20:return True
        if family=='overhead' and t>50 and up>.45 and e>20:return True
        if family in {'pulldown','pull_up'} and t>40 and up>.35 and e>20:return True
        if family=='y_raise' and t>50 and up>.3 and a>25 and quantile(rows,'elbow')>125:return True
        if family=='raise' and t>55 and a>25 and quantile(rows,'elbow')>125:return True
        if family in {'fly','reverse_fly'} and t>45 and (a>18 or span(rows,'wrist_x')>.25):return True
        if family=='chest_press' and t>55 and e>20:return True
        if family=='row' and t>40 and e>20 and quantile(rows,'wrist_up')<.3:return True
        if family=='face_pull' and t>45 and e>18 and up>-.25:return True
        if family in {'pushdown','curl'} and t>45 and e>20 and quantile(rows,'arm')<60 and up<.6:return True
        if family=='extension' and t>45 and e>20 and quantile(rows,'arm')>110:return True
    return False
