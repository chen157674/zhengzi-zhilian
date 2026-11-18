"""训练区间的前/中/后8帧计划；同源多窗口不冒充独立视频。"""
import numpy as np


def temporal_centres(start,end):
    if type(start) is not int or type(end) is not int or start<0 or end-start<1200:raise ValueError('连续训练区间不足或无效')
    centre=(start+end)/2
    return (centre,) if end-start<6400 else (start+1600,centre,end-1600)


def make_views(rows,resolve_times):
    if len({r['id'] for r in rows})!=len(rows):raise ValueError('训练片段身份重复')
    views=[]
    for index,row in enumerate(rows):
        if row.get('partition')!='train' or row.get('evaluation_only') or row.get('exclude_from_training') or row.get('model_inference_allowed_before_freeze') is False:raise ValueError('请选择训练来源的时间窗')
        centres=temporal_centres(row['start_ms'],row['end_ms']);middle=(row['start_ms']+row['end_ms'])/2
        for position,centre in enumerate(centres):
            if centre==middle:times=list(row['actual_ms']);kind='middle'
            else:
                start=centre-1600;desired=np.linspace(start,start+3100,8);times=list(resolve_times(row,desired));kind='early' if position==0 else 'late'
                if np.max(np.abs(np.asarray(times,dtype=float)-desired))>100:raise ValueError('训练时间窗真实采样偏移过大')
            if len(times)!=8 or any(type(t) is not int for t in times) or np.any(np.diff(times)<=0) or times[0]<row['start_ms'] or times[-1]>=row['end_ms']:raise ValueError('训练时间窗必须是区间内8个递增真实帧')
            views.append({**row,'id':row['id']+'--'+kind,'base_id':row['id'],'base_index':index,'view_kind':kind,'actual_ms':times,
                          'control_actual_ms':list(row['actual_ms'])})
    return views
