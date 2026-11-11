"""补提取同一次Full姿态推理的世界坐标，用于复用已有健身时序模型，不重提图像特征。"""
import argparse,hashlib,json,os,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment,project_path
configure_environment()
import av,numpy as np
from fitness_app.gym_pose import GymPoseService
from fitness_app.pose_input import input_policy_snapshot
from fitness_app.media import display_rgb
from fitness_app.automatic_recognition import CORE_ACTIONS
from evaluate_xclip import sources,OUT


def run(only=None,include_legacy_train=False,refresh_source_range=False):
    entries=sources(include_legacy=True)
    # 旧四核心已有模型/缓存；先补新动作和所有开发验证，避免重做整个旧训练集。
    if not include_legacy_train:entries=[r for r in entries if not (r['id'].startswith('legacy-') and r['partition']=='train' and r['action_id'] in CORE_ACTIONS)]
    policy=input_policy_snapshot(GymPoseService)
    for i,entry in enumerate(entries):
        if only and not entry.get('source_id','').startswith(only):continue
        target=OUT/(entry['id']+'-full-world-features.npz')
        identity={'source_sha256':entry['source_sha256'],'start_ms':entry['start_ms'],'end_ms':entry['end_ms'],'crop':entry['crop'],
                  'sampling':'middle <=6.4s at actual source frames nearest 10Hz; no forward-filled joints','pose':policy,
                  'display_code':hashlib.sha256((ROOT/'fitness_app/media.py').read_bytes()).hexdigest()}
        signature=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
        if target.exists():
            with np.load(target,allow_pickle=False) as cache:
                if str(cache['signature'])==signature:continue
                previous=json.loads(str(cache['identity']))
                if not refresh_source_range or {k:v for k,v in previous.items() if k not in ('start_ms','end_ms')}!={k:v for k,v in identity.items() if k not in ('start_ms','end_ms')}:raise ValueError('世界坐标缓存身份改变')
        path=project_path(entry['path'])
        if hashlib.sha256(path.read_bytes()).hexdigest()!=entry['source_sha256']:raise ValueError('世界坐标源视频摘要改变')
        center=(entry['start_ms']+entry['end_ms'])/2;span=min(6400,entry['end_ms']-entry['start_ms']);start=round(center-span/2);end=round(center+span/2)
        arrays={k:[] for k in ('timestamps','world','points','people','sizes')};next_stamp=start
        with GymPoseService(video=True) as pose,av.open(str(path)) as video:
            video.seek(start*1000)
            for frame in video.decode(video=0):
                if frame.time is None:continue
                stamp=round(float(frame.time)*1000)
                if stamp<next_stamp:continue
                if stamp>=end:break
                rgb=display_rgb(frame)
                if entry['crop']:
                    height,width=rgb.shape[:2];left,top,right,bottom=entry['crop'];rgb=np.ascontiguousarray(rgb[round(top*height):round(bottom*height),round(left*width):round(right*width)])
                result=pose.detect(rgb,stamp);world=np.full((33,5),np.nan,np.float32);points=np.full((33,5),np.nan,np.float32)
                if len(result['poses'])==1 and len(result.get('world_poses',[]))==1:
                    world=np.asarray([[p[k] for k in ('x','y','z','visibility','presence')] for p in result['world_poses'][0]],np.float32)
                    points=np.asarray([[p[k] for k in ('x','y','z','visibility','presence')] for p in result['poses'][0]],np.float32)
                for key,value in dict(timestamps=stamp,world=world,points=points,people=len(result['poses']),sizes=(rgb.shape[1],rgb.shape[0])).items():arrays[key].append(value)
                while next_stamp<=stamp:next_stamp+=100
        partial=target.with_suffix('.npz.part');owned=False
        try:
            with partial.open('xb') as output:
                owned=True;np.savez_compressed(output,signature=signature,identity=json.dumps(identity,sort_keys=True),**{k:np.asarray(v) for k,v in arrays.items()})
            os.replace(partial,target)
        finally:
            if owned and partial.exists():partial.unlink()
        print(json.dumps({'world_ready':i+1,'total':len(entries),'id':entry['id'],'frames':len(arrays['timestamps'])}),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--only');parser.add_argument('--include-legacy-train',action='store_true');parser.add_argument('--refresh-source-range',action='store_true');args=parser.parse_args();run(args.only,args.include_legacy_train,args.refresh_source_range)
