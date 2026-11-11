"""12：只用固定开发样本和已有姿态缓存诊断周期，不运行新姿态推理。"""
import json
import math
from dataclasses import asdict
from pathlib import Path
import re
import sys
from collections import Counter,defaultdict,deque

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
from fitness_app.paths import ROOT,project_path
from fitness_app.upstream import load_upstream_definition
from fitness_app.research_data import load_dataset
from fitness_app.research_extract import atomic_json,key_for
from fitness_app.frame_pipeline import usable_point
from fitness_app.action_counting import ActionCountConfig,ActionCounter,side_observation
from fitness_app.cycles import CycleConfig

SOURCES={'bodyweight_squat':('squat','start',-1),'standard_push_up':('push_up','start',-1),
    'standing_dumbbell_curl':('bicep_curl','down',-1),'conventional_deadlift':('deadlift','down',1)}
VERSION='cycles-four-actions-development-1'


def selected_samples(samples,action):
    # 按来源类别平衡，并跨人物轮转；不看计数输出挑容易片段。
    result=[]
    for label in (('GB','PB') if action=='conventional_deadlift' else ('good','bad')):
        eligible=[sample for sample in samples if sample.action_id==action and sample.partition in {'train','development'} and sample.label==label
            and any(view in (('front','diagonal') if action=='standing_dumbbell_curl' else ('side',)) for view in sample.views)]
        groups=defaultdict(list)
        for sample in eligible:groups[sample.subject_group].append(sample)
        count=0
        while count<6:
            advanced=False
            for group in sorted(groups):
                if groups[group]:result.append(groups[group].pop(0));count+=1;advanced=True
                if count==6:break
            if not advanced:raise ValueError('该动作／来源类别不足 6 个开发片段')
    return result


def cached_frames(sample,baseline,apply_bounds=True):
    entry=baseline['extractions'][sample.key]
    feature=json.loads((ROOT/'data/experiments/features'/f"{entry['feature_key']}.json").read_text(encoding='utf-8'))
    path=project_path(feature['pose_cache']);path.relative_to(ROOT/'data/experiments/poses')
    with np.load(path,allow_pickle=False) as cache:
        meta=json.loads(str(cache['metadata']));stat=sample.absolute_path.stat()
        if meta['pose_key']!=entry['pose_key'] or meta['source']!={'source_sha256':sample.sha256,'bytes':stat.st_size,'modified_ns':stat.st_mtime_ns}:raise ValueError('开发姿态缓存与源身份不一致')
        frames=[]
        for index,stamp in enumerate(cache['timestamps_ms']):
            count=int(cache['person_counts'][index]);points=[]
            if count==1:
                for point in cache['points'][index]:
                    value=dict(zip(('x','y','z','visibility','presence'),map(float,point)));value['valid']=usable_point(value);points.append(value)
            frames.append({'frame_seq':index,'captured_ms':int(stamp),'width':int(cache['sizes'][index,0]),'height':int(cache['sizes'][index,1]),
                'identity_accepted':bool(cache['identity_accepted'][index]),'framing':{'person_count':count,'landmarks':points}})
    if apply_bounds:
        from development_content_bounds import apply_verified_bounds
        return apply_verified_bounds(frames,sample.action_id,sample.absolute_path,sample.sha256)
    return frames


def provisional(action):
    name,state,direction=SOURCES[action];source=load_upstream_definition(name)
    match=re.fullmatch(r'angle\s*(?:>|<=)\s*(\d+(?:\.\d+)?)',source['states'][state]['condition'])
    if not match:raise ValueError('上游起点表达式不能按已核形式读取，不执行 eval')
    cycle=CycleConfig(float(match.group(1)),direction,10,20,5,200,100,100,200,500,round(source['min_rep_duration']*1000),30000)
    smoothing=source.get('smoothing',{});window=smoothing.get('window',3) if smoothing.get('enabled') else 1
    return ActionCountConfig(cycle,window,5,.02,5,120,VERSION)


def p95(values):return float(np.percentile(values,95,method='linear')) if values else None


def derive(action,frames_by_sample,chosen):
    initial=provisional(action);noise=defaultdict(list);spans=[]
    start_threshold=initial.cycle.start_threshold;start_diagnosis={'upstream_threshold':start_threshold}
    if action=='bodyweight_squat':
        # 核查图中站立已可观察，但部分髋投影峰值低于上游 165°。仅用开发人物
        # 的上部观测带确定捕获起点的范围；返回仍须接近本次实际稳定锚值。
        group_peaks=defaultdict(list);by_key={sample.key:sample for sample in chosen}
        for key,frames in frames_by_sample.items():
            candidates=defaultdict(list)
            for frame in frames:
                if not frame['identity_accepted'] or frame['framing']['person_count']!=1:continue
                for side in ('left','right'):
                    obs=side_observation(action,frame['framing']['landmarks'],frame['width'],frame['height'],side,initial)
                    if obs and obs['start_posture']:candidates[side].append(obs['values']['primary'])
            if candidates:
                values=max(candidates.values(),key=len)
                group_peaks[by_key[key].subject_group].append(float(np.percentile(values,90,method='linear')))
        peaks=[max(values) for values in group_peaks.values()]
        if len(peaks)<3:raise ValueError('缺少至少三个开发人物的可观察站立带')
        start_threshold=min(start_threshold,5*math.floor((float(np.percentile(peaks,10,method='linear'))-3)/5))
        start_diagnosis.update(group_upper_band=group_peaks,selected_threshold=start_threshold,
            method='每片可观察站立候选的 P90；同组取较高值，跨组 P10 减 3° 后向下取 5° 网格，仅调起点捕获带，不把该值当返回锚或质量标准。')
    for frames in frames_by_sample.values():
        if frames:spans.append(frames[-1]['captured_ms']-frames[0]['captured_ms'])
        previous={}
        for frame in frames:
            if not frame['identity_accepted'] or frame['framing']['person_count']!=1:previous.clear();continue
            for side in ('left','right'):
                obs=side_observation(action,frame['framing']['landmarks'],frame['width'],frame['height'],side,initial)
                if obs is None or not obs['start_posture'] or initial.cycle.direction*(obs['values']['primary']-start_threshold)>0:
                    previous.pop(side,None);continue
                window=previous.setdefault(side,deque(maxlen=3));stamp=frame['captured_ms']
                if window and not 0<stamp-window[-1][0]<=500:window.clear()
                window.append((stamp,obs['values']))
                if len(window)==3:
                    first,middle,last=window;fraction=(middle[0]-first[0])/(last[0]-first[0])
                    for key,value in middle[1].items():
                        trend=first[1][key]+fraction*(last[1][key]-first[1][key]);noise[key].append(abs(value-trend))
    if not noise['primary']:raise ValueError('开发片段未提供可用起点变化，不臆造参数')
    change=p95(noise['primary']);exit_delta=max(5.,math.ceil(3*change));minimum=max(15.,2*exit_delta);turn=max(3.,math.ceil(1.5*change))
    cycle=CycleConfig(start_threshold,initial.cycle.direction,exit_delta,minimum,turn,
        200,100,100,200,500,initial.cycle.minimum_cycle_ms,max(30000,math.ceil(max(spans)*2/1000)*1000),
        return_tolerance=180-start_threshold if initial.cycle.direction<0 else exit_delta/2)
    knee=max(3.,math.ceil(3*(p95(noise['knee']) or 0)))
    descent=max(.02,round(3*(p95(noise['body_descent']) or 0),4))
    trunk=max(3.,math.ceil(3*(p95(noise['trunk']) or 0)))
    config=ActionCountConfig(cycle,initial.smoothing_window,knee,descent,trunk,120,VERSION)
    return config,{'start_band':start_diagnosis,'near_start_trend_residuals':{key:{'observations':len(values),'p95':p95(values)} for key,values in noise.items()},
        'method':'用连续三帧中点相对局部线性趋势的绝对残差代替一阶运动变化，离线估参数、在线仍因果处理。退出带为残差 P95 的 3 倍且至少 5°；最小运动至少 15° 且为退出带 2 倍；转向带为 P95 的 1.5 倍且至少 3°。仅作工程分段初值，不是质量或人体标准。',
        'return_band':'稳定起点以确认窗口中位数锚定，离开不追随运动。上方站立／支撑／低位伸臂以原起点阶段带确认返回，不把更完全伸展当计数质量门槛；硬拉下方准备额外要求回到实际锚值附近（退出带一半）。返回时须仍具备该动作起点形态，起身离地不补成俯卧撑。',
        'duration_ms':{'start_confirm':200,'departure_confirm':100,'turn_confirm':100,'return_confirm':200,'maximum_gap':500,
            'minimum_cycle_source':'上游 min_rep_duration','maximum_cycle_source':'至少 30 秒且覆盖选中开发片段最大源跨度的 2 倍'},
        'support_knee_minimum_status':'120° 为区分明显屈膝支撑的开发候选，须结合画面诊断，不作为质量合格阈值'}


def main():
    samples=load_dataset();baseline=json.loads((ROOT/'data/experiments/baseline.json').read_text(encoding='utf-8'))
    report={'schema_version':1,'algorithm_version':VERSION,'truth_status':'development_diagnostic_no_independent_rep_truth','actions':{}}
    parameters={}
    for action in SOURCES:
        chosen=selected_samples(samples,action);frames_by_sample={sample.key:cached_frames(sample,baseline) for sample in chosen}
        config,provenance=derive(action,frames_by_sample,chosen);cases=[]
        for sample in chosen:
            counter=ActionCounter(action,config);events=[];reasons=Counter();phases=Counter()
            for frame in frames_by_sample[sample.key]:
                result=counter.process(frame,{'stream_epoch':sample.key,'identity_accepted':frame['identity_accepted']})
                events.extend(result['events']);observation=result['observation']
                if observation.get('reason'):reasons[observation['reason']]+=1
                for side,state in observation.get('sides',{}).items():
                    phases[side+':'+state['phase']]+=1
                    if state.get('reason'):reasons[side+':'+state['reason']]+=1
            cases.append({'sample_key':sample.key,'source_path':sample.path,'subject_group':sample.subject_group,'view':sample.views,
                'source_clip_label':sample.label,'events':events,'phase_frames':dict(phases),'reason_frames':dict(reasons),
                'observed_counts':dict(Counter(event['side'] for event in events)),'accuracy':None})
        parameters[action]=asdict(config);report['actions'][action]={'config':asdict(config),'provenance':provenance,'cases':cases}
        print(json.dumps({'action':action,'config':asdict(config),'cases':[{'source':case['source_path'],'counts':case['observed_counts']} for case in cases]},ensure_ascii=False),flush=True)
    report['parameters_sha256']=key_for(parameters)
    path=ROOT/'data/verification/step-12/development-cycles.json';atomic_json(path,report)
    # 尚未冻结／开放；正式接入前须检查开发诊断和对应真实事件连接。
    import hashlib
    atomic_json(ROOT/'data/models/cycle-counting.json',{'schema_version':1,'status':'development','algorithm_version':VERSION,'actions':parameters,
        'calibration_path':path.relative_to(ROOT).as_posix(),'calibration_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'parameters_sha256':report['parameters_sha256']})


if __name__=='__main__':main()
