"""将用户指定的本机视频制成兼容演示；原片只读，所有产物留在健身项目。"""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment,project_path
configure_environment()
import av
from PIL import Image
from fitness_app.media import probe_video,video_frames
from fitness_app.pose import PoseService,DEFAULT_MODEL
from fitness_app.frame_pipeline import analysis_framing
from fitness_app.posture_engine import create_analysis_engine
from fitness_app.coaching import coaching_observation,coaching_snapshot,committed_issues
from fitness_app.demo_feedback import build_demo_feedback,FEEDBACK_SCHEMA_VERSION
from fitness_app.posture_rules import policy_version
from fitness_app.research_extract import atomic_json

SOURCE=Path(r'E:\08_Downloads\LocalSend')
FFMPEG=Path(r'E:\07_Projects\07_MonetizationTests\02_KnowledgeVideos\open-source\huashu-art-motion\.venv\Scripts\ffmpeg.exe')
MANIFEST=ROOT/'data/presentation-assets.json'
CASES=(
    ('1575','pec_deck_fly','蝴蝶机夹胸'),
    ('8910','incline_dumbbell_press','上斜哑铃卧推'),
    ('9248','cable_face_pull','绳索面拉'),
    ('9250','cable_face_pull','绳索面拉'),
)


def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(4*1024*1024),b''):h.update(block)
    return h.hexdigest()


def asset(path):
    return {'path':path.relative_to(ROOT).as_posix(),'bytes':path.stat().st_size,'sha256':digest(path)}


def current(item,key,path):
    expected=item.get(key) or {}
    return path.is_file() and expected.get('path')==path.relative_to(ROOT).as_posix() and expected.get('bytes')==path.stat().st_size and expected.get('sha256')==digest(path)


def convert(source,target):
    with av.open(str(source)) as container:
        codec=container.streams.video[0].codec_context
        hdr=codec.color_trc in (16,18)
    filters=['fps=30','scale=1280:720:force_original_aspect_ratio=decrease:force_divisible_by=2']
    if hdr:filters+=['zscale=t=linear:npl=100','format=gbrpf32le','zscale=p=bt709','tonemap=tonemap=hable:desat=0','zscale=t=bt709:m=bt709:r=tv']
    filters+=['format=yuv420p','setsar=1']
    command=[str(FFMPEG),'-hide_banner','-loglevel','error','-n','-threads','2','-i',str(source),
             '-map','0:v:0','-an','-sn','-map_metadata','-1','-vf',','.join(filters),'-filter_threads','2',
             '-c:v','libx264','-threads','2','-preset','fast','-crf','23','-pix_fmt','yuv420p',
             '-color_primaries','bt709','-color_trc','bt709','-colorspace','bt709','-movflags','+faststart',str(target)]
    subprocess.run(command,check=True,creationflags=subprocess.CREATE_NO_WINDOW)
    return {'codec':'h264','pixel_format':'yuv420p','fps':30,'audio_removed':True,'hdr_to_sdr':hdr,'rotation_applied':True,'resize':'aspect_preserving_max_1280x720','filters':filters}


def poster(video,path):
    with av.open(str(video)) as container:
        stream=container.streams.video[0]
        container.seek(int(float(stream.duration)*.25),stream=stream)
        frame=next(container.decode(stream));image=frame.to_image();image.thumbnail((960,720))
        image.save(path,quality=86)
        return image.width,image.height


def track(video,path,action,info,source_sha):
    rows=[];statuses={};engine=create_analysis_engine(action);previous=-1000
    with PoseService(video=True) as detector:
        for stamp,rgb in video_frames(video,info):
            if stamp-previous<65:continue
            previous=stamp
            detected=detector.detect(rgb,stamp)
            framing=analysis_framing(action,detected)
            frame={'frame_seq':len(rows),'captured_ms':stamp,'width':rgb.shape[1],'height':rgb.shape[0],'framing':framing}
            result=engine.process(frame,{'coaching_view':'auto','stream_epoch':'home-user-demo'})
            observation=coaching_observation(result)
            cue=coaching_snapshot({'status':'running','action_id':action,'supported_items':engine.rules},
                                  {**frame,'analysis':observation},committed_issues(result))
            points=framing.get('landmarks',[])
            visible=framing.get('person_count')==1 and len(points)==33
            values=[[round(p['x'],6),round(p['y'],6)] if p.get('valid') and 0<=p['x']<=1 and 0<=p['y']<=1 else None for p in points] if visible else [None]*33
            for item in observation.get('posture',{}).get('items',[]):
                key=item['code']+':'+item['status'];statuses[key]=statuses.get(key,0)+1
            rows.append({'t':stamp,'p':values,'people':framing.get('person_count',0),'cue':cue.get('primary_cue'),
                         'feedback':build_demo_feedback(frame,result,engine.rules,cue)})
    doc={'schema_version':1,'source_sha256':source_sha,'width':info['width'],'height':info['height'],
         'mode':'preprocessed_user_source_not_live','frames':rows,'events':[],
         'accuracy_status':'no_independent_ground_truth','action_id':action,
         'pose_model_sha256':digest(DEFAULT_MODEL),'rule_version':engine.version,
         'new_pose_inferences':len(rows),'visible_frames':sum(any(p is not None for p in r['p']) for r in rows),
         'posture_status_counts':statuses,'no_personal_training_records_created':True}
    doc['feedback_schema_version']=FEEDBACK_SCHEMA_VERSION
    doc['feedback_implementation_sha256']=digest(ROOT/'fitness_app/demo_feedback.py')
    atomic_json(path,doc)
    return doc


def prepare():
    if not SOURCE.is_dir() or not FFMPEG.is_file():raise RuntimeError('已确认来源或现有FFmpeg入口不可用')
    doc=json.loads(MANIFEST.read_text(encoding='utf-8'))
    output=project_path('data/datasets/user-demos');output.mkdir(exist_ok=True)
    static=project_path('fitness_app/static/demo');static.mkdir(exist_ok=True)
    for number,action,title in CASES:
        source=SOURCE/f'IMG_{number}.MOV'
        if not source.is_file():raise FileNotFoundError(source)
        before=(source.stat().st_size,source.stat().st_mtime_ns);source_sha=digest(source)
        demo_id='user-'+number;old=doc.get('demos',{}).get(demo_id,{})
        if old and old.get('source_sha256')!=source_sha:raise RuntimeError('原片已变化，请先复核再替换: '+source.name)
        video=output/(demo_id+'.mp4');image=static/(demo_id+'-poster.jpg')
        if video.exists() and not current(old,'video',video):raise RuntimeError('未登记或被修改的交付视频，拒绝覆盖: '+str(video))
        converted=old.get('conversion') if current(old,'video',video) else convert(source,video)
        info=probe_video(video)
        if not image.exists():width,height=poster(video,image)
        elif current(old,'poster',image):
            with Image.open(image) as im:width,height=im.size
        else:raise RuntimeError('拒绝覆盖未知演示封面')
        item={'id':demo_id,'action_id':action,'title':title,'source_name':source.name,'source_sha256':source_sha,
              'source_bytes':before[0],'source_type':'user_supplied','source_url':None,'source_label':'用户提供的本机训练视频',
              'credit':'用户提供','license':'用户授权用于本项目演示','non_endorsement':'示范素材不代表动作完全正确；分析结果与个人训练统计分开。',
              'use_scope':'本项目演示与纠错验证；不加入识别训练集、不上传第三方AI',
              'video':asset(video),'poster':{**asset(image),'width':width,'height':height},'conversion':converted,
              'width':info['width'],'height':info['height'],'duration_ms':info['duration_ms']}
        if number=='1575':
            path=static/(demo_id+'-track.json')
            if path.exists() and current(old,'track',path):
                data=json.loads(path.read_text(encoding='utf-8'))
                if data.get('feedback_schema_version')!=FEEDBACK_SCHEMA_VERSION or data.get('feedback_implementation_sha256')!=digest(ROOT/'fitness_app/demo_feedback.py') or data.get('rule_version')!=policy_version():
                    data=track(video,path,action,info,item['video']['sha256'])
            elif path.exists():raise RuntimeError('拒绝覆盖未知展示轨迹')
            else:data=track(video,path,action,info,item['video']['sha256'])
            item.update(track=asset(path),frames=len(data['frames']),visible_frames=data['visible_frames'],rule_version=data['rule_version'])
        if before!=(source.stat().st_size,source.stat().st_mtime_ns) or source_sha!=digest(source):raise RuntimeError('来源传输中或内容改变，停止登记')
        doc.setdefault('demos',{})[demo_id]=item
        atomic_json(MANIFEST,doc)
        print(json.dumps({'demo':demo_id,'title':title,'video_bytes':item['video']['bytes'],'duration_ms':info['duration_ms'],'visible_frames':item.get('visible_frames')}),flush=True)
    doc.setdefault('home_demo_id','user-1575');doc.setdefault('active_demo_ids',['user-1575'])
    atomic_json(MANIFEST,doc)


if __name__=='__main__':prepare()
