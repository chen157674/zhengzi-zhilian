"""复用已捕获公开帧，区分单帧检测与视频跟踪历史；不改变正式模型配置。"""
import hashlib
import json
from importlib.metadata import version
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT, configure_environment
configure_environment()
import numpy as np
from PIL import Image
from fitness_app.pose import PoseService, DEFAULT_MODEL


def main():
    folder=ROOT/'data/verification/step-19'
    if '--two-person' in sys.argv:
        return two_person(folder)
    image=folder/'jack-ambiguous-frame.png'
    original=json.loads(image.with_suffix('.json').read_text(encoding='utf-8'))
    identity={'image_sha256':hashlib.sha256(image.read_bytes()).hexdigest(),
              'pose_service_sha256':hashlib.sha256((ROOT/'fitness_app/pose.py').read_bytes()).hexdigest(),
              'mediapipe':version('mediapipe'),'model_sha256':json.loads(DEFAULT_MODEL.with_name('manifest.json').read_text(encoding='utf-8'))['sha256'],
              'protocol':1}
    output=folder/'pose-ambiguity-comparison.json'
    existing=json.loads(output.read_text(encoding='utf-8')) if output.exists() else {'schema_version':1,'runs':[]}
    if any(r['identity']==identity for r in existing['runs']):
        print('Same captured-frame comparison already recorded; no repeated inference.')
        return
    rgb=np.ascontiguousarray(np.asarray(Image.open(image).convert('RGB')))
    with PoseService(video=False) as detector:
        still=detector.detect(rgb)
    repeated=[]
    with PoseService(video=True) as tracker:
        for stamp in range(0,660,60):
            result=tracker.detect(rgb,stamp)
            repeated.append({'at_ms':stamp,'pose_count':len(result['poses'])})
    data={'identity':identity,'source_id':original['source_id'],
          'scope':'Same already captured public RGB frame; one IMAGE inference and 11 repeated VIDEO inputs in a fresh instance, 60ms apart. Not a motion sequence, accuracy or performance acceptance.',
          'captured_original_video_pose_count':len(original['poses']),
          'fresh_image_pose_count':len(still['poses']),'fresh_image_poses':still['poses'],
          'fresh_video_same_frame':repeated,
          'production_unchanged':True,'confirmed_root_cause':False}
    existing['runs'].append(data)
    output.write_text(json.dumps(existing,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in data.items() if k not in ('identity','fresh_image_poses')},ensure_ascii=False))


def two_person(folder):
    contact=ROOT/'data/verification/step-14/sit-up-source.png'
    tiles={'10.01s':[320,0,640,180],'34.00s':[0,205,320,385],
           '50.02s':[640,205,960,385],'82.02s':[640,410,960,590]}
    identity={'image_sha256':hashlib.sha256(contact.read_bytes()).hexdigest(),
              'pose_service_sha256':hashlib.sha256((ROOT/'fitness_app/pose.py').read_bytes()).hexdigest(),
              'mediapipe':version('mediapipe'),'model_sha256':json.loads(DEFAULT_MODEL.with_name('manifest.json').read_text(encoding='utf-8'))['sha256'],
              'crops':tiles,'resize':[640,360],'protocol':2}
    output=folder/'pose-ambiguity-comparison.json'
    existing=json.loads(output.read_text(encoding='utf-8')) if output.exists() else {'schema_version':1,'runs':[]}
    if any(r['identity']==identity for r in existing['runs']):
        print('Same two-person crop diagnostic already recorded; no repeated inference.')
        return
    sheet=Image.open(contact).convert('RGB');assert sheet.size==(1280,820)
    observations=[]
    with PoseService(video=False) as detector:
        for label,box in tiles.items():
            rgb=np.ascontiguousarray(np.asarray(sheet.crop(box).resize((640,360),Image.Resampling.BICUBIC)))
            result=detector.detect(rgb)
            observations.append({'source_review_time':label,'visible_people_from_codex_review':2,'detected_poses':len(result['poses']),
                                 'poses':result['poses']})
    data={'identity':identity,'source_id':'dvids-1016256','scope':'Four full-image tiles from retained public source review contact sheet, two visible persons in each as reviewed by Codex; native tile 320x180 enlarged to 640x360; single scene/people, not independent person-count truth or motion/occlusion acceptance.',
          'mode':'IMAGE num_poses=2, unchanged default confidence thresholds','observations':observations,'production_unchanged':True}
    existing['runs'].append(data);output.write_text(json.dumps(existing,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps([{k:v for k,v in o.items() if k!='poses'} for o in observations],ensure_ascii=False))


if __name__=='__main__':
    main()
