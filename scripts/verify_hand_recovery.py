"""复用原片身体缓存，真实重跑局部手模型并对照折腕覆盖；不上传私人帧。"""
import argparse,copy,hashlib,json,os,sys,time
from collections import Counter
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment,project_path
configure_environment()
os.environ['FITNESS_POSE_DEVICE']='cpu'
from fitness_app.rtmpose_input import RTMPose,recover_wrist_hands
from fitness_app.movement_guidance import MovementGuidance,wrist_observation
from fitness_app.posture_engine import resolve_view
from fitness_app.media import video_frames,probe_video
from fitness_app.research_extract import atomic_json

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
OUTPUT=ROOT/'data/verification/round3/hand-recovery-review.json'

def images(model):
    import numpy as np
    from PIL import Image
    source=ROOT/'data/verification/round3/pretrained-input-review.json';original=json.loads(source.read_text(encoding='utf-8'));rows=[]
    for row in original['rows']:
        path=project_path(row['source_image_path'])
        if sha(path)!=row['source_image_sha256']:raise ValueError('原标注图摘要改变')
        rgb=np.array(Image.open(path).convert('RGB'));h,w=rgb.shape[:2]
        recovered,detail=recover_wrist_hands(row['body'],row['subject_hands'],model,rgb)
        def observed(hands):
            frame={'captured_ms':0,'width':w,'height':h,'pretrained':{'status':'observed','timestamp_ms':0,'body':row['body'],'hands':hands,
                'body_model_sha256':row['body']['model_sha256'],'hand_model_sha256':model.resource['sha256']}}
            return {side:wrist_observation(frame,side) for side in ('left','right')}
        rows.append({'id':row['id'],'image_sha256':sha(path),'before':observed(row['subject_hands']),
            'after':observed(recovered),'recovery':detail,'hands':recovered})
    return {'source_sha256':sha(source),'rows':rows}

def videos(model,numbers):
    source=ROOT/'data/verification/round3/movement-guidance-review.json';cache=json.loads(source.read_text(encoding='utf-8'))
    assets=json.loads((ROOT/'data/presentation-assets.json').read_text(encoding='utf-8'));manifest=json.loads((ROOT/'data/annotation/x-anylabeling/manifest.json').read_text(encoding='utf-8'));results=[]
    for number in numbers:
        original=next(v for v in cache['videos'] if v['number']==number);asset=assets['demos']['user-'+number]
        path=project_path(asset['video']['path'])
        if sha(path)!=original['source_sha256']:
            candidates=[project_path(v['path']) for v in manifest['videos'] if v['number']==number]
            path=next((p for p in candidates if p.is_file() and sha(p)==original['source_sha256']),None)
            if path is None:raise ValueError('找不到与缓存完全同源原片：'+number)
        by={row['t']:row for row in original['frames']};observers={name:MovementGuidance(original['action_id'],'hand-recovery-review') for name in ('before','after')}
        counts={name:Counter() for name in observers};issues={name:[] for name in observers};rows=[];began=time.monotonic()
        for stamp,rgb in video_frames(path,probe_video(path)):
            if stamp not in by or stamp%200:continue
            row=by[stamp];body=row.get('body');hands=row.get('hands',[]);detail={'attempted':0,'recovered':0}
            recovered,detail=recover_wrist_hands(body,hands,model,rgb) if body and row['identity_accepted'] else (hands,detail)
            frame={'captured_ms':stamp,'frame_seq':stamp,'width':rgb.shape[1],'height':rgb.shape[0],
                'identity_accepted':row['identity_accepted'],'framing':{'person_count':row['base_people'],
                'usable':row['identity_accepted'],'landmarks':row['original_landmarks']},
                'pretrained':{'status':'observed' if body else 'unassessable','timestamp_ms':stamp,'body':body,'hands':hands,
                'body_model_sha256':body.get('model_sha256') if body else None,'hand_model_sha256':model.resource['sha256']}}
            frame['posture_view']=resolve_view(frame,'auto')[0];checks={}
            for name,selected in (('before',hands),('after',recovered)):
                current={**frame,'pretrained':{**frame['pretrained'],'hands':selected}}
                items,findings=observers[name].update(current,{})
                counts[name].update(i['code']+':'+i['status'] for i in items)
                issues[name].extend(i for i in findings if i['status']=='issue')
                checks[name]={side:wrist_observation(current,side) for side in ('left','right')}
            rows.append({'t':stamp,'checks':checks,'recovery':detail,'hands':recovered})
        results.append({'number':number,'source_path':path.relative_to(ROOT).as_posix(),'video_sha256':sha(path),
            'cache_sha256':sha(source),'counts':{k:dict(v) for k,v in counts.items()},'issues':issues,'rows':rows,
            'elapsed_seconds':round(time.monotonic()-began,3),'sample_interval_ms':200})
        print(json.dumps({'video':number,'frames':len(rows),'counts':{k:dict(v) for k,v in counts.items()},
            'issue_codes':{k:dict(Counter(i['code'] for i in v)) for k,v in issues.items()}},ensure_ascii=False),flush=True)
    return results

def native_replay(model):
    path=ROOT/'data/verification/round3/hand-native-input-review.json'
    if not path.exists():return None
    document=json.loads(path.read_text(encoding='utf-8'));source=ROOT/'data/datasets/user-demos/user-1694.mp4'
    assets=json.loads((ROOT/'data/presentation-assets.json').read_text(encoding='utf-8'))
    if sha(source)!=assets['demos']['user-1694']['video']['sha256']:raise ValueError('当前原片身份改变')
    by={row['t']:row for row in document['rows']};observer=MovementGuidance('machine_chest_press','native-hand-recovery');counts=Counter();issues=[]
    for stamp,rgb in video_frames(source,probe_video(source)):
        if stamp not in by:continue
        row=by[stamp];data=row['pretrained'];hands,detail=recover_wrist_hands(data['body'],data['hands'],model,rgb)
        recovered={**data,'hands':hands,'hand_recovery':detail}
        frame={'captured_ms':stamp,'width':rgb.shape[1],'height':rgb.shape[0],'identity_accepted':True,
            'framing':{'person_count':1,'usable':True,'landmarks':row['landmarks']},'pretrained':recovered}
        frame['posture_view']=resolve_view(frame,'auto')[0];items,findings=observer.update(frame,{})
        counts.update(i['status'] for i in items if i['code'].endswith('wrist_bend'))
        issues.extend(i for i in findings if i['status']=='issue' and i['code'].endswith('wrist_bend'))
        row['recovered_pretrained']=recovered;row['recovered_wrists']={side:wrist_observation(frame,side) for side in ('left','right')}
    document.update(adaptive_crop_counts=dict(counts),adaptive_crop_issues=issues,source_sha256=sha(source),
        implementation_sha256={n:sha(ROOT/'fitness_app'/n) for n in ('rtmpose_input.py','pretrained_input.py','movement_guidance.py')})
    atomic_json(path,document)
    print('native_recovery',dict(counts),'saved_candidates',len(issues),flush=True)
    return {'report':path.relative_to(ROOT).as_posix(),'sha256':sha(path),'source_sha256':sha(source),'counts':dict(counts),'issue_observations':len(issues)}

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--videos',nargs='*',default=['1693','1694','1575']);args=parser.parse_args()
    model=RTMPose('hand',device='cpu')
    try:
        report={'scope':'same_person_development_not_independent_specific_grip_truth','paid_api_calls':0,'private_media_uploaded':False,
            'new_body_inferences':0,'hand_model_sha256':model.resource['sha256'],
            'implementation_sha256':{name:sha(ROOT/'fitness_app'/name) for name in ('rtmpose_input.py','pretrained_input.py','movement_guidance.py')},
            'images':images(model),'videos':videos(model,args.videos),'native_replay':native_replay(model)}
        atomic_json(OUTPUT,report)
        print('image_observed', {name:sum(v['status']=='observed' for r in report['images']['rows'] for v in r[name].values()) for name in ('before','after')},flush=True)
    finally:model.close()
