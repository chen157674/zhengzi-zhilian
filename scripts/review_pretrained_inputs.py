"""固定八图比较预训练身体/手部输入；人工主体框与自动手定位来源分开记录。"""
import json
import math
from pathlib import Path
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment,project_path
configure_environment()
from fitness_app.rtmpose_input import RTMPose,PretrainedHands,quality_landmarks,associate_hands
from fitness_app.pretrained_input import body_features
from fitness_app.scene_reference import estimate_vertical,lateral_measurement
from scripts.prepare_pretrained_correction import atomic_json,update,sha
from scripts.train_grip_localizer import match


def review():
    from PIL import Image,ImageDraw
    import numpy as np
    body_model=RTMPose('body');hand_model=PretrainedHands()
    manifest=json.loads((ROOT/'data/annotation/x-anylabeling/manifest.json').read_text(encoding='utf-8'))
    rows=[];panels=[];totals={'tp':0,'fp':0,'fn':0}
    for row in manifest['frame_annotations']:
        image=Image.open(project_path(row['image_path'])).convert('RGB');rgb=np.array(image);width,height=image.size
        start=time.perf_counter();body=body_model.detect(rgb,[0,0,width,height]);raw=hand_model.detect(rgb)
        hands,other=associate_hands(body,raw['hands'],width,height)
        labels=json.loads(project_path(row['native_json']).read_text(encoding='utf-8'))
        targets=[]
        for shape in labels['shapes']:
            if shape['label']=='hand':
                xs,ys=zip(*shape['points']);targets.append({'class_id':0,'box':[min(xs),min(ys),max(xs),max(ys)]})
        predictions=[{'class_id':0,'box':h['bbox'],'confidence':h['score']} for h in hands]
        counts=match(predictions,targets)['hand']
        for k in ('tp','fp','fn'):totals[k]+=counts[k]
        mapped=quality_landmarks(body);reference=estimate_vertical(rgb,mapped)
        frame={'captured_ms':row['actual_ms'],'width':width,'height':height,'identity_accepted':True,
               'framing':{'landmarks':mapped,'person_count':1,'scene_reference':reference}}
        lateral,reason=lateral_measurement(frame)
        def line_angle(left,right):
            a,b=body['keypoints'][left],body['keypoints'][right]
            if min(a['score'],b['score'])<.5 or not a['coordinate_valid'] or not b['coordinate_valid']:return None
            dx=(b['x']-a['x'])*width;dy=(b['y']-a['y'])*height
            angle=math.degrees(math.atan2(dy,dx));return ((angle+90)%180)-90
        rows.append({'id':row['id'],'source_image_path':row['image_path'],'source_image_sha256':row['image_sha256'],
            'source_subject_crop_is_manual':True,'body':body,'body_features':body_features(body,width,height),'hands':raw,'subject_hands':hands,
            'unassociated_hand_candidates':len(other),'target_hand_counts':counts,'scene_reference':reference,
            'lateral':lateral,'lateral_unknown_reason':reason,'eye_line_degrees':line_angle(1,2),
            'shoulder_line_degrees':line_angle(5,6),'elapsed_seconds':round(time.perf_counter()-start,3)})
        draw=ImageDraw.Draw(image)
        for edge in ((5,6),(5,7),(7,9),(6,8),(8,10),(5,11),(6,12),(11,12)):
            a,b=[body['keypoints'][i] for i in edge]
            if a['coordinate_valid'] and b['coordinate_valid'] and min(a['score'],b['score'])>=.5:
                draw.line((a['x']*width,a['y']*height,b['x']*width,b['y']*height),fill='#00d5ff',width=2)
        for hand in hands:
            draw.rectangle(hand['bbox'],outline='#ff9b29',width=2)
            points=hand['keypoints']
            for chain in ((0,1,2,3,4),(0,5,6,7,8),(0,9,10,11,12),(0,13,14,15,16),(0,17,18,19,20)):
                for a,b in zip(chain,chain[1:]):
                    p,q=points[a],points[b]
                    if p['coordinate_valid'] and q['coordinate_valid'] and min(p['score'],q['score'])>=.3:
                        draw.line((p['x']*width,p['y']*height,q['x']*width,q['y']*height),fill='#62ec6d',width=1)
        image.thumbnail((480,400));panel=Image.new('RGB',(490,430),'white');panel.paste(image,(0,25))
        ImageDraw.Draw(panel).text((5,5),row['id']+' RTMPose + RTMDet',fill='black');panels.append(panel)
    report={'schema_version':1,'scope':'same_person_eight_context_images_development_not_independent_accuracy',
        'body_model_sha256':body_model.resource['sha256'],'hand_model_sha256':hand_model.pose.resource['sha256'],
        'hand_detector_sha256':hand_model.detector.resource['sha256'],'hand_detector_threshold':.3,
        'finger_keypoint_response_threshold':.3,'body_keypoint_response_threshold':.5,
        'target_association':'nearest_unassigned_box_to_body_wrist_with_normalized_distance_at_most_1.25',
        'target_hand_counts':totals,'rows':rows,'production_enabled':False,'private_media_upload_allowed':False}
    out=ROOT/'data/verification/round3/pretrained-input-review.json';atomic_json(out,report)
    sheet=Image.new('RGB',(490*2,430*4),'white')
    for index,panel in enumerate(panels):sheet.paste(panel,((index%2)*490,(index//2)*430))
    sheet.save(out.with_suffix('.jpg'),quality=93)
    for name in ('rtmpose-body','rtmpose-hand','rtmdet-hand'):
        registry=json.loads((ROOT/'data/experiments/open-source-correction/manifest.json').read_text(encoding='utf-8'))
        record=registry['models'][name];record.update(runtime_validated=True,runtime_evidence=out.relative_to(ROOT).as_posix())
        update('models',name,record)
    print(json.dumps({'images':len(rows),'target_hand_counts':totals,'rows':[
        {'id':r['id'],'hands':len(r['subject_hands']),'hand_states':[h['observation']['state'] for h in r['subject_hands']],
         'lateral_degrees':r['lateral']['angle'] if r['lateral'] else None,'shoulder_degrees':r['shoulder_line_degrees'],
         'eye_degrees':r['eye_line_degrees']} for r in rows]},ensure_ascii=False),flush=True)


if __name__=='__main__':review()
