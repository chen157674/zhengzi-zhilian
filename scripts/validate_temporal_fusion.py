"""在冻结的新来源缓存上核对双模态一致性；暂不改变产品发布开关。"""
import sys,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment
configure_environment()
import numpy as np
from collections import deque,Counter
from fitness_app.temporal_gym import TemporalGym,agreement,JOINTS
from fitness_app.gym_recognition import trained_resources,load_head,motion_evidence
from fitness_app.gym_motion import span
from fitness_app.automatic_recognition import body_frame
from fitness_app.visual_recognition import normalize
from fitness_app.basic_recognition import BasicRecognizer

CASES=[('commons-bench-fitnessscape','barbell_bench_press',0,7100),('commons-pullups-fitnessscape','pull_up',0,6000),
       ('commons-cdc-chest-press','machine_chest_press',18000,30000)]
OUT=ROOT/'data/verification/step-22'


def run():
    head,_=trained_resources();classifier=load_head(head['sha256']);reports=[]
    for key,expected,start,end in CASES:
        with np.load(OUT/f'{key}-{start}-{end}.npz',allow_pickle=False) as a:data={k:a[k].copy() for k in a.files if k!='metadata'}
        with np.load(OUT/f'{key}-{start}-{end}-full-world.npz',allow_pickle=False) as a:world={k:a[k].copy() for k in a.files if k!='metadata'}
        temporal=TemporalGym();frames=deque(maxlen=40);images=deque(maxlen=5);outputs=[];pending=None;since=None;counts=Counter()
        baseline=BasicRecognizer();baseline.temporal.enabled=False;baseline._load()
        combined=BasicRecognizer();combined.temporal.enabled=True;combined._load()
        baseline_counts=Counter();combined_counts=Counter()
        for i,stamp in enumerate(data['timestamps']):
            people=int(data['people'][i]);p=data['points'][i];poses=[[dict(zip(('x','y','z','visibility','presence'),map(float,v))) for v in p]] if people==1 else [[]]*people
            f=body_frame({'poses':poses,'content_bounds':data['bounds'][i].tolist()},int(stamp),*map(int,data['sizes'][i]),i)
            index=int(np.argmin(np.abs(world['timestamps']-stamp)));values=[{} for _ in range(33)]
            if abs(int(world['timestamps'][index])-int(stamp))<=20 and world['people'][index]==1:
                for n,joint in enumerate(JOINTS):values[joint]=dict(zip(('x','y','z'),map(float,world['world'][index,n])))
            f['world_landmarks']=values;frames.append(f)
            feature=data['features'][i]
            actual_feature=feature if np.isfinite(feature).all() else None
            baseline_counts[baseline.process(f,actual_feature)['action_id'] or 'unknown']+=1
            combined_counts[combined.process(f,actual_feature)['action_id'] or 'unknown']+=1
            if np.isfinite(feature).all():images.append((int(stamp),feature))
            if people!=1:frames.clear();images.clear();pending=None;since=None;continue
            if i%3 or len(images)<3:continue
            pose=temporal.predict(list(frames));scores=classifier.predict_proba(normalize(np.mean([v for _,v in images],axis=0))[None])[0]
            visual=dict(zip(map(str,classifier.classes_),map(float,scores)));fused=agreement(pose,visual)
            if fused:
                evidence=motion_evidence(fused['candidate_action_id'],list(frames))
                if not evidence or not any(span(r,'elbow')>=12 or span(r,'arm')>=12 for r in evidence['sides'].values()):fused=None
            candidate=fused['candidate_action_id'] if fused else None
            if candidate!=pending:pending=candidate;since=int(stamp)
            confirmed=candidate if candidate and int(stamp)-since>=1000 else None
            counts[confirmed or 'unknown']+=1
            outputs.append({'stamp':int(stamp),'pose_label':pose.get('label'),'pose_score':pose.get('score'),'visual_top':max(visual,key=visual.get),
                            'visual_score':max(visual.values()),'candidate':candidate,'confirmed':confirmed})
        reports.append({'source_id':key,'expected':expected,'counts':dict(counts),'samples':outputs,
                        'actual_pipeline_without_temporal':dict(baseline_counts),'actual_pipeline_with_temporal':dict(combined_counts)})
        print(json.dumps({'source_id':key,'baseline':dict(baseline_counts),'combined':dict(combined_counts)}),flush=True)
    (OUT/'temporal-fusion.json').write_text(json.dumps({'scope':'offline_development_agreement','production_enabled':False,'reports':reports},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


if __name__=='__main__':run()
