"""仅回放已校验真实姿态缓存的新运动统计，不重新分类或姿态推理。"""
import hashlib,json,sys
from pathlib import Path
import numpy as np

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from fitness_app.paths import configure_environment
configure_environment()
from fitness_app.offline_video import NumericFrame
from fitness_app.posture_engine import create_analysis_engine
from fitness_app.movement_stability import Collector,report,VERSION


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    out=ROOT/'data/verification/round3';cache=out/'upload-real-cache.npz';source=out/'upload-real-regression.mp4'
    previous=json.loads((out/'upload-real-regression.json').read_text(encoding='utf-8'));recipe=json.loads((out/'upload-real-source.json').read_text(encoding='utf-8'))
    if sha(cache)!=previous['numeric_cache_sha256'] or sha(source)!=recipe['sha256']:raise ValueError('缓存或原片身份已改变，不能套用旧验证')
    values=[]
    with np.load(cache,allow_pickle=False) as arrays:
        for part_index,part in enumerate(recipe['parts']):
            action=part['action_id']
            if action is None:continue
            start,end=part['composite_start_ms'],part['composite_end_ms'];collector=Collector(action,start);engine=create_analysis_engine(action);windows=[];rows=0
            for i,stamp in enumerate(arrays['timestamps']):
                if not start<=int(stamp)<end:continue
                row=NumericFrame(int(stamp),*map(int,arrays['sizes'][i]),arrays['points'][i],arrays['world'][i],int(arrays['people'][i]),tuple(map(float,arrays['bounds'][i])),str(arrays['pose_models'][i]),bool(arrays['identity_accepted'][i]))
                frame=row.frame(action,rows);good=frame['framing']['usable'] and row.identity_accepted
                output=engine.process(frame,{'session_id':'cached-replay','stream_epoch':'cached-replay','set_index':1,'coaching_view':'auto','active_ms':row.stamp-start}) if good else {}
                if not good:engine.reset('tracking_lost');frame['identity_accepted']=False
                windows.extend(collector.push(frame,output,1,str(part_index)));rows+=1
            windows.extend(collector.finish());compared=report(windows)
            values.append({'action_id':action,'source_part':part_index,'source_range_ms':[start,end],'cached_pose_frames':rows,
                'window_count':len(windows),'comparisons':compared,'windows':windows})
    result={'scope':'known_source_cached_pose_replay_not_independent_accuracy_or_human_fatigue_truth','exercise_basis':'source_known_action_not_new_classification',
        'policy_version':VERSION,'policy_code_sha256':sha(ROOT/'fitness_app/movement_stability.py'),'source_recipe':'data/verification/round3/upload-real-source.json',
        'source_sha256':sha(source),'numeric_cache_sha256':sha(cache),'new_pose_calls':0,'classification_calls':0,'paid_ai_calls':0,
        'independent_specific_error_accuracy':None,'parts':values}
    (out/'movement-stability-real-replay.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    print(json.dumps({'new_pose_calls':0,'classification_calls':0,'parts':[{'action':v['action_id'],'windows':v['window_count'],'compared':sum(x['state']=='compared' for x in v['comparisons'])} for v in values]},ensure_ascii=False))


if __name__=='__main__':main()
