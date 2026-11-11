"""同源缓存身体点＋真实背景参考重放；少量真实帧比较YOLO/手部输入，不训练或调用API。"""
from collections import Counter
import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT, configure_environment, project_path
configure_environment()
from fitness_app.media import probe_video, video_frames
from fitness_app.hand_pose import HandPoseService, correction_resource
from fitness_app.scene_reference import estimate_vertical, lateral_measurement
from fitness_app.posture_engine import create_analysis_engine
from fitness_app.posture_rules import policy_version
from fitness_app.research_extract import atomic_json

OUTPUT = ROOT/'data/verification/round3'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def review():
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont
    from ultralytics import YOLO, settings
    settings.update({'sync':False,'datasets_dir':str(ROOT/'data/datasets'),
                     'weights_dir':str(ROOT/'data/models'),'runs_dir':str(OUTPUT/'yolo-runs')})
    model_path, yolo_resource = correction_resource('yolo')
    yolo = YOLO(str(model_path))
    manifest = json.loads((ROOT/'data/presentation-assets.json').read_text(encoding='utf-8'))
    results, previews = [], []
    font = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 18)
    with HandPoseService() as hands:
        for number in ('1692','1693','1694'):
            demo = manifest['demos']['user-'+number]
            video = project_path(demo['video']['path'])
            track_path = project_path(demo['track']['path'])
            track = json.loads(track_path.read_text(encoding='utf-8'))
            if digest(video)!=demo['video']['sha256'] or digest(track_path)!=demo['track']['sha256'] or track['source_sha256']!=demo['video']['sha256']:
                raise ValueError('视频/轨迹来源摘要不匹配，不重放')
            info = probe_video(video)
            frames = {row['t']:row for row in track['frames']}
            stamps = list(frames)
            wanted = {min(stamps,key=lambda t:abs(t-info['duration_ms']*fraction)) for fraction in (.25,.5,.75)}
            engine = create_analysis_engine(demo['action_id'],include_candidates=True)
            counts, reasons = Counter(), Counter()
            samples, events = [], []
            begin = time.monotonic()
            for stamp,rgb in video_frames(video,info):
                if stamp not in frames:
                    continue
                row = frames[stamp]
                p = [{'x':point[0],'y':point[1],'z':0.,'visibility':1.,'presence':1.,'valid':True}
                     if point is not None else {'valid':False} for point in row['p']]
                ref = estimate_vertical(rgb,p)
                frame = {'captured_ms':stamp,'frame_seq':len(events),'width':rgb.shape[1],'height':rgb.shape[0],
                    'identity_accepted':any(point is not None for point in row['p']),
                    'framing':{'person_count':row['people'],'code':'VISIBLE','landmarks':p,'scene_reference':ref}}
                output = engine.process(frame,{'coaching_view':'auto','stream_epoch':'correction-replay-'+number})
                item = next(x for x in output['observation']['posture']['items'] if x['code'].endswith('trunk_lateral_lean'))
                counts[item['status']]+=1
                if item.get('reason'):reasons[item['reason']]+=1
                events.append({'t':stamp,'status':item['status'],'reason':item.get('reason'),
                               'value':item['evidence'].get('value'),'side':item.get('side'), 'reference':ref})
                if stamp in wanted:
                    hand = hands.detect(rgb,stamp)
                    prediction = yolo.predict(source=rgb[:,:,::-1].copy(),device='cpu',imgsz=640,verbose=False,save=False)[0]
                    sample = {'t':stamp,'input_width':rgb.shape[1],'input_height':rgb.shape[0],
                        'mediapipe_cached_body_people':row['people'], 'hands':hand,
                        'yolo_people':len(prediction.boxes),
                        'yolo_keypoints':prediction.keypoints.data.cpu().tolist() if prediction.keypoints is not None else [],
                        'lateral':item}
                    samples.append(sample)
                    image = Image.fromarray(rgb).resize((480,270))
                    panel = Image.new('RGB',(480,315),'white');panel.paste(image,(0,0))
                    value = item['evidence'].get('value')
                    label = f'{number}  {stamp/1000:.1f}秒  '+('侧倾%.1f°'%value if value is not None else '参考不足')
                    ImageDraw.Draw(panel).text((8,274),label,fill='#202020',font=font)
                    previews.append(panel)
            result = {'number':number,'source_sha256':demo['video']['sha256'],'cached_track_sha256':demo['track']['sha256'],
                'classification':'asset_action_used_for_quality_replay_not_new_classification',
                'body_source':'same_video_valid_2d_points_from_existing_track_not_new_pose_inference',
                'status_counts':dict(counts),'unassessable_reasons':dict(reasons), 'samples':samples, 'events':events,
                'elapsed_seconds':round(time.monotonic()-begin,2)}
            results.append(result)
            print(json.dumps({k:result[k] for k in ('number','status_counts','unassessable_reasons','elapsed_seconds')},ensure_ascii=False),flush=True)
    document = {'schema_version':1,'rule_version':policy_version()+'-candidate','scope':'development_replay_not_independent_accuracy',
                'implementation_sha256':{name:digest(ROOT/'fitness_app'/name) for name in ('scene_reference.py','hand_pose.py','gym_quality.py','posture_engine.py')},
                'yolo_resource_sha256':yolo_resource['sha256'],'videos':results,'no_api_calls':True,'no_training':True}
    atomic_json(OUTPUT/'correction-input-review.json',document)
    sheet = Image.new('RGB',(1440,315*3),'#eeeeee')
    for i,panel in enumerate(previews):sheet.paste(panel,((i%3)*480,(i//3)*315))
    sheet.save(OUTPUT/'correction-input-review.jpg',quality=90)
    registry_path = ROOT/'data/models/correction-resources.json'
    registry = json.loads(registry_path.read_text(encoding='utf-8'))
    for name in ('hands','yolo'):
        registry['resources'][name]['runtime_validated']=True
        registry['resources'][name]['runtime_evidence']='data/verification/round3/correction-input-review.json'
    atomic_json(registry_path,registry)


if __name__=='__main__':
    argparse.ArgumentParser(description=__doc__).parse_args()
    review()
