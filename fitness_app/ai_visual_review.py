"""将云端可见观察绑定本次真实样本；不充当本地检出或连续错误区间。"""
import re

RULES={
    'side_lean':('身体向一侧偏斜',['躯干','肩','髋'],
        '先稳定坐姿或站姿，让肩髋保持对齐，再以可控制的负重完成动作。'),
    'back_rounding':('腰背轮廓呈明显圆弧',['背部','髋'],
        '先暂停核对背部位置，以较轻负重练习髋部折叠，保持自然可控制的背部姿态。'),
    'elbow_wrist_alignment':('肘腕位置需要检查',['肘','腕'],
        '核对同侧肘和腕的位置，减小幅度并保持手臂运动可控制。'),
    'wrist_bend':('手腕明显折弯',['腕','手'],
        '检查手腕与前臂的位置，保持可控制的握持，避免强行折腕。'),
}
PROMPT="""
本次姿态画面复核请额外返回visual_findings数组，最多4项。只核查图片实际可见的身体偏斜，不依本地未检出或来源说明反推结果。
每项仅含code、status、frame_indices、observation。code只能为side_lean（身体侧倾）、back_rounding（可见腰背圆弧）、elbow_wrist_alignment（肘腕投影关系）、wrist_bend（折腕）；status只能为observed、not_observed、unassessable。
frame_indices是支持该结论的本次图片序号（从1开始、升序、不得引用未发送的帧），observation是一句可见部位描述。没有证据或遮挡时用unassessable，不要默认正确；没有实际评估的项目不要输出。frame_indices不可以用时间、角度或数量替代。
observed只表示列出图片中可见该偏差，不声称完整错误时段或全片结论。身体在器械某条竖线旁边本身不等于侧倾，要观察肩髋相对方向；躯干整体前倾不等于圆背。肘腕判断仅适用于相应推举姿态；看不清手掌不可判断折腕或握持。不要推断发力、握力、伤病或精确脊柱曲度。左右不明确时只说向一侧，不写人体左右。
无本地引用证据时explanations及next_focus仍为空，visual_findings单独记录画面观察。summary先直接回答，然后是其余字段和visual_findings。例：{"summary":"抽样画面中躯干向一侧偏斜。","explanations":[],"next_focus":[],"limitations":[],"visual_findings":[{"code":"side_lean","status":"observed","frame_indices":[1,2],"observation":"肩部中点相对髋部中点持续偏向一侧。"}]}
"""


def validate_findings(raw,bundle,text):
    visual=bundle.get('facts',{}).get('visual_input') or {}
    frames=visual.get('frames',[])
    if not bundle.get('visual_form_review') or not frames:
        if raw is not None:raise ValueError('结构化画面观察缺少本次附图')
        return None
    if raw is None:
        return {'state':'unassessable','reason':'structured_findings_not_returned','findings':[],
            'source':'deepseek_image_observation_not_local_detector','whole_video_correctness':None}
    if not isinstance(raw,list) or len(raw)>4:raise ValueError('画面问题条目无效')
    findings=[];seen=set()
    for item in raw:
        if not isinstance(item,dict) or set(item)!={'code','status','frame_indices','observation'}:
            raise ValueError('画面问题字段无效')
        code=item['code'];status=item['status'];indices=item['frame_indices']
        if not isinstance(code,str) or code not in RULES or code in seen:
            raise ValueError('画面问题类别无效或重复')
        seen.add(code)
        if status not in ('observed','not_observed','unassessable'):raise ValueError('画面问题状态无效')
        if (not isinstance(indices,list) or not 1<=len(indices)<=len(frames) or
            any(type(v) is not int or not 1<=v<=len(frames) for v in indices) or indices!=sorted(set(indices))):
            raise ValueError('画面问题引用了不存在或无序的图片')
        observation=text(item['observation'],300)
        if re.search(r'[左右](?:侧|边|肘|腕|肩|髋|膝|手|脚|腿|臂)',observation):
            raise ValueError('画面复核不补造人体左右侧')
        title,parts,suggestion=RULES[code]
        samples=[{'frame_index':i,'source_ms':frames[i-1]['source_ms'],'sha256':frames[i-1]['sha256']} for i in indices]
        times=[v['source_ms'] for v in samples]
        findings.append({'code':code,'status':status,'title':title,'body_parts':parts,
            'observation':observation,'suggestion':suggestion if status=='observed' else '',
            'samples':samples,'sample_span_ms':[min(times),max(times)],
            'time_scope':'sampled_frames_only_not_continuous_error_interval',
            'source':'deepseek_image_observation_not_local_detector','evidence_level':'visual_reference',
            'side':'none','local_detector_confirmed':False})
    return {'state':'observations_returned' if findings else 'unassessable',
        'reason':None if findings else 'no_visible_items_returned','findings':findings,
        'source':'deepseek_image_observation_not_local_detector','whole_video_correctness':None}
