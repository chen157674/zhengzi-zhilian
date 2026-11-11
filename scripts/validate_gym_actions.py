"""新增真实输入的增量核查；缓存姿态/图像特征，不以演示视频充当错误真值。"""
import argparse,hashlib,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment,project_path
configure_environment()
import numpy as np
from fitness_app.pose_input import ContentPoseService,input_policy_snapshot
from fitness_app.gym_pose import GymPoseService
from fitness_app.basic_recognition import BasicRecognizer
from fitness_app.automatic_recognition import body_frame
from fitness_app.automatic_analysis import AutomaticAnalysis
from fitness_app.frame_pipeline import analysis_framing
from fitness_app.gym_recognition import resources
from collections import Counter

OUT=ROOT/'data/verification/step-22'
CASES=[('barbell_bench_press','dvids-639911',5000,16000,(.26,.5,.77,1.)),
       ('dumbbell_bench_press','dvids-636920',6000,20000,(.26,.5,.77,1.)),
       ('incline_dumbbell_press','dvids-636927',3000,15000,(.26,.5,.77,1.)),
       ('dumbbell_shoulder_press','dvids-636933',2000,15000,(.26,.5,.77,1.)),
       ('pec_deck_fly','commons-pec-deck-rear-deltoid',205000,230000,None),
       ('reverse_pec_deck_fly','commons-pec-deck-rear-deltoid',580000,605000,None),
       ('reverse_pec_deck_fly','commons-pec-deck-rear-deltoid',565000,580000,None),
       ('barbell_bench_press','commons-bench-fitnessscape',0,7100,None),
       ('pull_up','commons-pullups-fitnessscape',0,6000,None),
       ('machine_chest_press','commons-cdc-chest-press',18000,30000,None)]


def cache(case,entry,refresh_pose=False):
    action,key,start,end,crop=case;path=project_path(entry['path'])
    if hashlib.sha256(path.read_bytes()).hexdigest()!=entry['sha256']:raise ValueError('输入摘要改变')
    metadata={'source_sha256':entry['sha256'],'start_ms':start,'end_ms':end,'step_ms':100,'crop':list(crop) if crop else None,
              'pose':input_policy_snapshot(GymPoseService),'visual_backbone':json.loads((ROOT/'data/models/clip-vit-base-patch32/manifest.json').read_text(encoding='utf-8'))}
    target=OUT/f'{key}-{start}-{end}.npz'
    if target.exists():
        with np.load(target,allow_pickle=False) as a:
            previous=json.loads(str(a['metadata']));arrays={k:a[k].copy() for k in a.files if k!='metadata'}
        if previous==metadata:return arrays,True
        if refresh_pose:
            if {k:v for k,v in previous.items() if k!='pose'}!={k:v for k,v in metadata.items() if k!='pose'}:raise ValueError('刷新姿态不能改变输入和图像特征')
            import av
            indices={int(t):i for i,t in enumerate(arrays['timestamps'])}
            with GymPoseService(video=True) as pose,av.open(str(path)) as video:
                video.seek(start*1000)
                for f in video.decode(video=0):
                    stamp=round(float(f.time)*1000)
                    if stamp>=end:break
                    if stamp not in indices:continue
                    i=indices[stamp];rgb=f.to_ndarray(format='rgb24')
                    if crop:
                        h,w=rgb.shape[:2];left,top,right,bottom=crop
                        rgb=np.ascontiguousarray(rgb[round(top*h):round(bottom*h),round(left*w):round(right*w)])
                    result=pose.detect(rgb,stamp);arrays['people'][i]=len(result['poses']);arrays['points'][i]=np.nan
                    if len(result['poses'])==1:arrays['points'][i]=np.array([[p[k] for k in ('x','y','z','visibility','presence')] for p in result['poses'][0]])
                    arrays['bounds'][i]=result['content_bounds']
            np.savez_compressed(target,metadata=json.dumps(metadata,sort_keys=True),**arrays)
            print(json.dumps({'source':key,'reused_image_features':True,'changed_policy_pose_frames':len(indices)}),flush=True)
            return arrays,True
        # 本轮只新增零人体回退；ContentPoseService.detect与骨干、图像特征不变。
        if previous['pose']['version']!='content-roi-2':raise ValueError('已有缓存身份变化，需明确失效范围')
        if {k:v for k,v in previous.items() if k!='pose'}!={k:v for k,v in metadata.items() if k!='pose'}:raise ValueError('输入或图像特征身份改变')
        keys=('model_sha256','mediapipe','running_mode','num_poses','content_bounds','pose_service_sha256')
        if any(previous['pose'][k]!=metadata['pose'][k] for k in keys):raise ValueError('原姿态资源已改变')
        import av
        needed={int(t):i for i,t in enumerate(arrays['timestamps']) if arrays['people'][i]==0}
        with GymPoseService(video=True) as pose,av.open(str(path)) as video:
            video.seek(start*1000)
            for f in video.decode(video=0):
                stamp=round(float(f.time)*1000)
                if stamp>=end:break
                if stamp not in needed:continue
                i=needed[stamp];rgb=f.to_ndarray(format='rgb24')
                if crop:
                    h,w=rgb.shape[:2];left,top,right,bottom=crop
                    rgb=np.ascontiguousarray(rgb[round(top*h):round(bottom*h),round(left*w):round(right*w)])
                h,w=rgb.shape[:2];left,top,right,bottom=arrays['bounds'][i]
                result=pose.recover(rgb,stamp,{'poses':[],'model':'cached-full','input_transform':{'crop_pixels':[round(left*w),round(top*h),round(right*w),round(bottom*h)]}})
                arrays['people'][i]=len(result['poses'])
                if len(result['poses'])==1:arrays['points'][i]=np.array([[p[k] for k in ('x','y','z','visibility','presence')] for p in result['poses'][0]])
        np.savez_compressed(target,metadata=json.dumps(metadata,sort_keys=True),**arrays)
        print(json.dumps({'source':key,'reused_image_features':True,'fallback_frames':len(needed)}),flush=True)
        return arrays,True
    import av
    visual=BasicRecognizer();arrays={k:[] for k in ('timestamps','points','people','sizes','bounds','features')};next_ms=start
    with GymPoseService(video=True) as pose,av.open(str(path)) as video:
        video.seek(start*1000)
        for f in video.decode(video=0):
            stamp=round(float(f.time)*1000)
            if stamp<next_ms:continue
            if stamp>=end:break
            rgb=f.to_ndarray(format='rgb24')
            if crop:
                h,w=rgb.shape[:2];left,top,right,bottom=crop
                rgb=np.ascontiguousarray(rgb[round(top*h):round(bottom*h),round(left*w):round(right*w)])
            result=pose.detect(rgb,stamp)
            vector=visual.extract(rgb,stamp)
            if not visual.available:raise RuntimeError(visual.reason)
            points=np.full((33,5),np.nan)
            if len(result['poses'])==1:points=np.array([[p[k] for k in ('x','y','z','visibility','presence')] for p in result['poses'][0]])
            for k,v in dict(timestamps=stamp,points=points,people=len(result['poses']),sizes=(rgb.shape[1],rgb.shape[0]),bounds=result['content_bounds'],features=vector if vector is not None else np.full(512,np.nan)).items():arrays[k].append(v)
            next_ms=stamp+100
    arrays={k:np.asarray(v) for k,v in arrays.items()};np.savez_compressed(target,metadata=json.dumps(metadata,sort_keys=True),**arrays)
    return arrays,False


def replay(case,arrays):
    recognizer=BasicRecognizer();recognizer._load();analysis=AutomaticAnalysis();decisions=Counter();reasons=Counter();tops=Counter();assessments=Counter();samples=[]
    gym,_=resources()
    for seq,stamp in enumerate(arrays['timestamps']):
        points=arrays['points'][seq];people=int(arrays['people'][seq]);w,h=map(int,arrays['sizes'][seq])
        poses=[[dict(zip(('x','y','z','visibility','presence'),map(float,p))) for p in points]] if people==1 else [[]]*people
        result={'poses':poses,'content_bounds':arrays['bounds'][seq].tolist()};frame=body_frame(result,int(stamp),w,h,seq)
        feature=arrays['features'][seq];feature=feature if np.isfinite(feature).all() else None
        if feature is not None:tops[gym['labels'][int(np.argmax(gym['features']@feature))]]+=1
        r=recognizer.process(frame,feature);decisions[r['action_id'] or 'unknown']+=1;reasons[r['reason'] or 'accepted']+=1
        if r['action_id']:frame['framing']=analysis_framing(r['action_id'],result)
        output=analysis.process(frame,r)
        for item in output.get('analysis',{}).get('posture',{}).get('items',[]):assessments[item['code']+':'+item['status']]+=1
        if seq%10==0:samples.append({'stamp':int(stamp),'action':r['action_id'],'reason':r['reason'],'similarity':r.get('visual_similarity'),'margin':r.get('visual_margin')})
    return {'action_label':case[0],'source_id':case[1],'start_ms':case[2],'end_ms':case[3],'crop':case[4],'frames':len(arrays['timestamps']),
            'decisions':dict(decisions),'reasons':dict(reasons),'image_top_labels':dict(tops),'assessments':dict(assessments),'samples':samples}


def run(only=None,refresh_pose=False):
    OUT.resolve().relative_to(ROOT);OUT.mkdir(exist_ok=True)
    entries={e['id']:e for e in json.loads((ROOT/'data/datasets/development-movements/sources.json').read_text(encoding='utf-8'))['sources']}
    cases=[c for c in CASES if only is None or c[1]==only];result=[]
    for case in cases:
        values,reused=cache(case,entries[case[1]],refresh_pose);row=replay(case,values);row['reused_cache']=reused;result.append(row)
        print(json.dumps({k:row[k] for k in ('source_id','frames','decisions','image_top_labels','reused_cache')},ensure_ascii=False),flush=True)
    report={'schema_version':1,'scope':'development_inputs_not_independent_accuracy','supervised_training':False,
            'recognition_version':BasicRecognizer().version,'cases':result}
    target=OUT/'real-inputs.json'
    if target.exists():
        old=json.loads(target.read_text(encoding='utf-8'))
        report['full_frame_initial_recheck']=old.get('full_frame_initial_recheck',old['cases'] if all('crop' not in c for c in old['cases']) else [])
    if only and target.exists():
        old=json.loads(target.read_text(encoding='utf-8'))
        report['cases']=[c for c in old['cases'] if c['source_id']!=only]+result
    target.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--only');parser.add_argument('--refresh-pose',action='store_true');args=parser.parse_args();run(args.only,args.refresh_pose)
