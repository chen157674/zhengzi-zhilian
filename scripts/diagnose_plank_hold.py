"""14-D 复用固定开发姿态诊断支撑区间，不运行模型或生成独立真值。"""
from collections import Counter
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from prepare_movement_input import cached_frames
from fitness_app.paths import ROOT
from fitness_app.plank_hold import PlankHoldConfig,PlankHoldCounter,plank_observation
from fitness_app.database import json_text


def development_config():
    return PlankHoldConfig(45.,135.,.55,.08,.8,130.,.07,200,500,'forearm-hold-development-1')


def diagnose():
    config=development_config();counter=PlankHoldCounter(config);frames=cached_frames('dvids-640237-side')
    intervals=[];reasons=Counter();observations=[]
    for frame in frames:
        result=counter.process(frame,{'stream_epoch':'development','identity_accepted':frame['identity_accepted']})
        intervals.extend(result['intervals']);reason=result['observation']['sides']['none']['reason'];reasons[reason or 'observable']+=1
        if frame['frame_seq']%15==0:
            observations.append({'at_ms':frame['captured_ms'],'sides':{side:plank_observation(frame,side,config) for side in ('left','right')}})
    merged=[]
    for item in intervals:
        if merged and merged[-1]['end_ms']==item['start_ms']:merged[-1]['end_ms']=item['end_ms']
        else:merged.append(item.copy())
    data=asdict(config);cache=ROOT/'data/verification/step-14/dvids-640237-side-poses.npz'
    report={'schema_version':1,'action_id':'forearm_plank','source_id':'dvids-640237-side','config':data,
        'parameters_sha256':hashlib.sha256(json_text(data).encode()).hexdigest(),'pose_cache_sha256':hashlib.sha256(cache.read_bytes()).hexdigest(),
        'frames':len(frames),'intervals':intervals,'merged_intervals':merged,'hold_ms':sum(i['end_ms']-i['start_ms'] for i in merged),
        'reason_frame_counts':dict(reasons),'observations':observations,
        'parameter_basis':'仅本开发侧面片段几何核查：以前臂姿态及膝相对肘—脚尖投影线的离地形态区分支撑／趴卧；长度使用肩踝投影距离，避免髋位变化改变尺度。膝投影高度 0.07、前臂角 45–135°、确认200ms为工程候选，不是质量／临床标准。',
        'evidence_status':'development_input_only_no_independent_hold_truth',
        'limitations':['单一人物与机位，支撑时间尚无独立人工区间真值','投影形态不能精确测量膝／脚／肘触地或接触力','完整原片为双机位分屏，本诊断只针对固定侧面面板，不支持多人拼接','髋高低质量未输出，保持量不使用髋线质量阈值']}
    p=ROOT/'data/verification/step-14/plank-hold-development.json';p.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:report[k] for k in ('merged_intervals','hold_ms','reason_frame_counts')},ensure_ascii=False))


if __name__=='__main__':diagnose()
