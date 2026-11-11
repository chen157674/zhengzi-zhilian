"""14-E 真实开发缓存及采样删减诊断，绝不补造丢失的张开阶段。"""
from collections import Counter
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from prepare_movement_input import cached_frames
from fitness_app.paths import ROOT
from fitness_app.cycles import CycleConfig
from fitness_app.jumping_jack import JumpingJackConfig,JumpingJackCounter,jack_observation
from fitness_app.database import json_text


def development_config():
    return JumpingJackConfig(CycleConfig(.45,1,.5,.8,.1,100,33,33,50,200,300,30000,.45),
        40.,.9,.4,45.,.6,.15,.3,3,'jumping-jack-development-2')


def run(frames,config):
    counter=JumpingJackCounter(config);events=[];reasons=Counter();transitions=[];previous=None
    for frame in frames:
        result=counter.process(frame,{'stream_epoch':'development','identity_accepted':frame['identity_accepted']})
        events.extend(result['events']);reason=result['observation']['sides']['none']['reason'];reasons[reason or 'observable']+=1
        state=(counter.tracker.phase,reason)
        if state!=previous:transitions.append({'at_ms':frame['captured_ms'],'phase':state[0],'reason':reason});previous=state
    return {'frames':len(frames),'events':events,'reason_frame_counts':dict(reasons),'transitions':transitions}


def diagnose():
    frames=cached_frames('commons-jumping-jacks-segment');config=development_config();result=run(frames,config);data=asdict(config)
    cache=ROOT/'data/verification/step-14/commons-jumping-jacks-segment-poses.npz'
    report={'schema_version':1,'action_id':'jumping_jack','source_id':'commons-jumping-jacks-segment','config':data,
        'parameters_sha256':hashlib.sha256(json_text(data).encode()).hexdigest(),'pose_cache_sha256':hashlib.sha256(cache.read_bytes()).hexdigest(),**result,
        'sampling_diagnostics':{str(stride):run(frames[::stride],config) for stride in (2,3,6)},
        'observations':[{'at_ms':f['captured_ms'],'geometry':jack_observation(f,config)} for f in frames[::3]],
        'parameter_basis':'上游最低周期0.3秒保留；闭合臂角按本开发片段40°候选，腿距按稳定起点肩宽归一。双臂至少45°变化、腿距至少0.6肩宽、每腿向外至少0.15肩宽是联合运动证据，不是手臂完全举高／腿宽的质量合格线。张开使用同帧联合证据，返回取较大进度并核实手脚都合拢。 开合总数不依赖解剖侧名，双足按画面位置排序；正面肩宽/躯干比例候选下限0.3覆盖本片段最小0.347的投影波动。可靠合拢用独立返回确认窗口，平滑预热不当作断帧；连续下一次复用已确认返回起点。',
        'evidence_status':'development_input_only_no_independent_ground_truth',
        'limitations':['单一人物、正面低机位，尚无独立逐次真值','原片含其他动作，本输入仅取先核定的首2.9秒；不支持自动混合动作识别','不由骨架推断精确离地／触地；低采样率缺少阶段时拒判，不补次数']}
    p=ROOT/'data/verification/step-14/jumping-jack-development.json';p.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'events':result['events'],'reasons':result['reason_frame_counts'],'sampled_counts':{k:len(v['events']) for k,v in report['sampling_diagnostics'].items()}},ensure_ascii=False))


if __name__=='__main__':diagnose()
