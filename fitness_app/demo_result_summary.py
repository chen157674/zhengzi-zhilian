"""将原始证据区间与已确认的视频回看标记分别汇总。"""


def evidence_ranges(track, duration_ms):
    ranges=[]
    for index,frame in enumerate(track['frames']):
        feedback=frame.get('feedback',{})
        checks=[row for row in feedback.get('checks',[]) if row.get('status')=='issue']
        if feedback.get('state')!='issue' or not checks:continue
        highlight=feedback.get('highlight') or {}
        start,end=highlight.get('evidence_start_ms'),highlight.get('evidence_end_ms')
        valid=(highlight.get('evidence_time_base')=='source_ms' and
               type(start) is int and type(end) is int and start<end and end>=0 and start<duration_ms)
        if valid:
            start,end=max(0,start),min(duration_ms,end)
            if start>=end:continue
        else:
            start=frame['t']
            end=track['frames'][index+1]['t'] if index+1<len(track['frames']) else duration_ms
        code=highlight.get('code') or checks[0]['code']
        side=highlight.get('side') or checks[0].get('side','none')
        title=feedback.get('title','需要调整');instruction=feedback.get('instruction','')
        key=(code,side,title,instruction)
        existing=next((row for row in reversed(ranges) if row['_key']==key and start<=row['end_ms']),None)
        if existing:
            existing['start_ms']=min(existing['start_ms'],start)
            existing['end_ms']=max(existing['end_ms'],end)
        else:
            ranges.append({'_key':key,'title':title,'instruction':instruction,'start_ms':start,'end_ms':end,
                           'seek_ms':frame['t'],'basis':'source_evidence' if valid else 'frame_feedback'})
    for row in ranges:row.pop('_key')
    return sorted(ranges,key=lambda row:(row['start_ms'],row['end_ms']))


def review_ranges(demo):
    value=demo.get('display_annotation') or {}
    if value.get('source')!='user_confirmed_demo_annotation' or value.get('scope')!='time_range':return []
    start,end=value.get('start_ms'),value.get('end_ms')
    titles=value.get('issues');instruction=value.get('instruction')
    if (type(start) is not int or type(end) is not int or not 0<=start<end<=demo['duration_ms'] or
        not isinstance(titles,list) or not titles or not all(isinstance(t,str) and t.strip() for t in titles) or
        not isinstance(instruction,str) or not instruction.strip()):return []
    return [{'source':'user_confirmed_demo_annotation','title':'、'.join(titles),'instruction':instruction,
             'start_ms':start,'end_ms':end,'label':'已确认的回看重点'}]


def timecode(milliseconds):
    # 与播放器源时间一致，保留一位小数；避免将通知出现时间当作问题持续范围。
    tenths=round(milliseconds/100)
    minutes,remaining=divmod(tenths,600)
    seconds,tenth=divmod(remaining,10)
    return f'{minutes:02d}:{seconds:02d}.{tenth}'
