"""可复用的逐片研究姿态缓存；独立测试的读取由后续冻结门禁控制。"""
import hashlib
import json
from pathlib import Path
import time

import numpy as np

from .database import json_text
from .paths import ROOT,project_path
from .pose import PoseService,DEFAULT_MODEL
from .media import video_frames,display_rgb,source_stamp
from .frame_pipeline import framing
from .sessions import IdentityContinuity
from .research_features import CONFIG,CONFIG_SHA256,frame_angles,aggregate_angles

OUTPUT=ROOT/'data/experiments'


def pose_config():
    model=json.loads((DEFAULT_MODEL.parent/'manifest.json').read_text(encoding='utf-8'))
    files=('media.py','pose.py','frame_pipeline.py','sessions.py')
    return {'schema_version':'research-pose-1','model_sha256':model['sha256'],'model':'mediapipe-full-float16-1','device':'CPU',
        'num_poses':2,'running_mode':'VIDEO','sampling':'all_decoded_frames','rotation':'display_matrix_right_angles',
        'resize':'long_side<=1280_short_side<=720','target':'single_pose_torso_continuity_v1','coordinate_space':'image_normalized',
        'implementation_sha256':{name:hashlib.sha256((ROOT/'fitness_app'/name).read_bytes()).hexdigest() for name in files}}


def key_for(value):return hashlib.sha256(json_text(value).encode()).hexdigest()


def probe_research_video(path):
    """研究源可用 MPEG-4 等既有编码；不套用用户上传的浏览器 H.264 门槛。"""
    import av
    with av.open(str(path),options={'protocol_whitelist':'file'}) as container:
        streams=list(container.streams.video)
        if len(streams)!=1:raise ValueError('研究片段视频轨不唯一')
        stream=streams[0];frame=next(container.decode(stream))
        origin=round(float(stream.start_time*stream.time_base)*1000) if stream.start_time is not None else 0
        source_stamp(frame,origin);display_rgb(frame)
        return {'origin_ms':origin,'rotation':frame.rotation,'codec':stream.codec_context.name}


def atomic_json(path,value):
    path=project_path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix(path.suffix+'.part');owned=False
    try:
        with temporary.open('xb') as output:
            owned=True;output.write((json_text(value)+'\n').encode('utf-8'))
        temporary.replace(path)
    finally:
        if owned and temporary.exists():temporary.unlink()


def extract(sample,config,allow_test=False,retry_failed=False):
    if sample.partition=='excluded':raise ValueError('隔离样本不能用于特征或拟合')
    if sample.partition=='test' and not allow_test:raise ValueError('未冻结前禁止读取独立测试视频')
    if sample.partition=='test':
        frozen_path=OUTPUT/'freeze.json';baseline_path=OUTPUT/'baseline.json'
        if not frozen_path.exists() or not baseline_path.exists():raise ValueError('独立测试缺少实际冻结记录')
        frozen=json.loads(frozen_path.read_text(encoding='utf-8'));baseline=json.loads(baseline_path.read_text(encoding='utf-8'))
        if not frozen.get('frozen') or not baseline.get('test_used') or sample.key not in frozen.get('test_keys',[]) or frozen.get('pose_config')!=config or frozen.get('feature_config_sha256')!=CONFIG_SHA256:
            raise ValueError('独立测试授权与冻结配置不匹配')
    pose_key=key_for({'source_sha256':sample.sha256,'action_id':sample.action_id,'pose':config})
    feature_key=key_for({'pose_key':pose_key,'features':CONFIG,'feature_implementation_sha256':hashlib.sha256((ROOT/'fitness_app/research_features.py').read_bytes()).hexdigest()})
    pose_path=project_path(OUTPUT/'poses'/f'{pose_key}.npz');feature_path=project_path(OUTPUT/'features'/f'{feature_key}.json')
    stat=sample.absolute_path.stat();identity={'source_sha256':sample.sha256,'bytes':stat.st_size,'modified_ns':stat.st_mtime_ns}
    if feature_path.exists():
        result=json.loads(feature_path.read_text(encoding='utf-8'))
        if result['source']!=identity or result['feature_key']!=feature_key:raise ValueError('源文件状态或特征缓存身份变化，不能复用')
        if result['status']!='failed' or not retry_failed:return {**result,'cache_hit':True}
    began=time.monotonic();rows=[]
    try:
        if pose_path.exists():
            with np.load(pose_path,allow_pickle=False) as cached:
                metadata=json.loads(str(cached['metadata']))
                if metadata['source']!=identity or metadata['pose_key']!=pose_key:raise ValueError('源文件状态或姿态缓存身份变化，不能复用')
                stamps=cached['timestamps_ms'];sizes=cached['sizes'];points=cached['points'];people=cached['person_counts'];accepted=cached['identity_accepted']
        else:
            timestamps=[];dimensions=[];landmarks=[];people=[];accepted=[];tracker=IdentityContinuity()
            info=probe_research_video(sample.absolute_path)
            with PoseService(video=True) as model:
                for stamp,rgb in video_frames(sample.absolute_path,info):
                    output=model.detect(rgb,stamp);height,width=rgb.shape[:2];poses=output['poses'];quality=framing(sample.action_id,output)
                    unique=len(poses)==1
                    same_person=unique and tracker.accept({'framing':quality,'width':width,'height':height})
                    # NaN 表示不存在的目标点，绝不作为可见性良好的零坐标送入特征。
                    point=np.asarray([[p[key] for key in ('x','y','z','visibility','presence')] for p in poses[0]],dtype=np.float32) if unique else np.full((33,5),np.nan,dtype=np.float32)
                    timestamps.append(stamp);dimensions.append((width,height));landmarks.append(point);people.append(len(poses));accepted.append(same_person)
            stamps=np.asarray(timestamps,dtype=np.int64);sizes=np.asarray(dimensions,dtype=np.int32);points=np.asarray(landmarks,dtype=np.float32);people=np.asarray(people,dtype=np.int16);accepted=np.asarray(accepted,dtype=np.bool_)
            pose_path.parent.mkdir(parents=True,exist_ok=True);temporary=pose_path.with_suffix('.npz.part');owned=False
            try:
                with temporary.open('xb') as stream:
                    owned=True;np.savez_compressed(stream,timestamps_ms=stamps,sizes=sizes,points=points,person_counts=people,identity_accepted=accepted,
                        metadata=np.asarray(json_text({'pose_key':pose_key,'source':identity,'config':config,'complete':True})))
                temporary.replace(pose_path)
            finally:
                if owned and temporary.exists():temporary.unlink()
        for index,stamp in enumerate(stamps):
            pose=[dict(zip(('x','y','z','visibility','presence'),map(float,p))) for p in points[index]]
            rows.append(frame_angles(sample.action_id,{'poses':[pose] if people[index]==1 else [],'width':int(sizes[index,0]),'height':int(sizes[index,1]),'identity_accepted':bool(accepted[index])}))
        result={**aggregate_angles(sample.action_id,rows),'sample_key':sample.key,'source':identity,'pose_key':pose_key,'feature_key':feature_key,
            'pose_cache':pose_path.relative_to(ROOT).as_posix(),'processed_ms':int(stamps[-1]) if len(stamps) else 0,'status':'completed','elapsed_ms':round((time.monotonic()-began)*1000)}
    except Exception as error:
        # 失败留在正式实验输入记录，不将未完成解码片段当成功向量。
        result={'sample_key':sample.key,'source':identity,'pose_key':pose_key,'feature_key':feature_key,'config_sha256':CONFIG_SHA256,
            'status':'failed','features':None,'error_type':type(error).__name__,'error_code':getattr(error,'code','FEATURE_EXTRACTION_FAILED'),
            'elapsed_ms':round((time.monotonic()-began)*1000)}
    atomic_json(feature_path,result)
    return {**result,'cache_hit':False}
