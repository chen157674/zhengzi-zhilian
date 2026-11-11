"""14-A 单一公开开发输入诊断；只读姿态缓存，不执行模型或正式效果评价。"""
import json
import hashlib
from collections import Counter
from dataclasses import asdict
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from prepare_movement_input import cached_frames
from fitness_app.paths import ROOT,project_path
from fitness_app.reverse_lunge import ReverseLungeCounter,ReverseLungeConfig,lunge_observation
from fitness_app.cycles import CycleConfig
from fitness_app.database import json_text


def development_config():
    # 投影量按本站开发片段的静止起点及实际阶段核查；这些是工程分段候选，
    # 不将单片段参数声称为人群标准／幅度合格线。真实效果仍需独立原始标签。
    return ReverseLungeConfig(CycleConfig(.18,1,.15,.35,.08,200,100,100,200,500,800,30000,.18),
        145.,15.,.08,.18,.18,.08,.5,.5,.04,.15,3,'reverse-lunge-development-1')


def diagnose():
    source='dvids-551833';frames=cached_frames(source);config=development_config();counter=ReverseLungeCounter(config)
    events=[];reasons=Counter();transitions=[];previous=None
    for frame in frames:
        result=counter.process(frame,{'identity_accepted':frame['identity_accepted'],'stream_epoch':'development'})
        events.extend(result['events']);reason=result['observation']['reason'];reasons[reason or 'observable']+=1
        state=(counter.tracker.phase,counter.active_side,reason)
        if state!=previous:transitions.append({'at_ms':frame['captured_ms'],'phase':state[0],'side':state[1],'reason':reason});previous=state
    config_data=asdict(config)
    source_file=project_path(ROOT/'data/verification/step-14/dvids-551833-poses.npz')
    result={'schema_version':1,'action_id':'reverse_lunge','source_id':source,'config':config_data,
        'parameters_sha256':hashlib.sha256(json_text(config_data).encode()).hexdigest(),
        'pose_cache_sha256':hashlib.sha256(source_file.read_bytes()).hexdigest(),'frames':len(frames),
        'events':events,'reason_frame_counts':dict(reasons),'transitions':transitions,
        'parameter_basis':'工程候选：投影按起点躯干长度归一，足跟到脚尖确定投影前方；脚返回 0.18、支撑脚移动 0.18、最小后撤 0.35、下沉 0.08 为开发片段几何核查参数；膝变化 15°用于联合运动证据，不是质量合格线。',
        'evidence_status':'development_input_only_no_independent_ground_truth',
        'limitations':['仅一个公开人物／机位，不能报告独立计数准确率','图像投影无法覆盖正面深度不可辨、双脚遮挡与骨架侧别长期错误','骨架不能识别是否持有器械；动作由用户显式选择，源片变式人工视觉核查（Codex）不是自动器械识别']}
    path=project_path(ROOT/'data/verification/step-14/reverse-lunge-development.json');path.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'events':events,'reasons':dict(reasons)},ensure_ascii=False))


if __name__=='__main__':diagnose()
