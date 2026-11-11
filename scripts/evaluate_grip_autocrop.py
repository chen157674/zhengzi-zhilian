"""固定参数诊断：自动手点裁剪→已训练定位候选；保留全上下文失败记录。"""
import json
import math
import sys
import time
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment,project_path
configure_environment()
from fitness_app.research_extract import atomic_json
from fitness_app.hand_pose import HandPoseService
from scripts.prepare_xanylabeling import digest
from scripts.train_grip_localizer import OUT,REPORT,NAMES,targets_from_json,match,iou


def evaluate():
    import numpy as np
    import torch
    from PIL import Image,ImageDraw
    from ultralytics import YOLO
    torch.set_num_threads(2)
    training=json.loads(REPORT.read_text(encoding='utf-8'));weight=project_path(training['weights_path'])
    if digest(weight)!=training['weights_sha256']:raise ValueError('训练后权重摘要变化')
    model=YOLO(str(weight),task='detect')
    manifest=json.loads((ROOT/'data/annotation/x-anylabeling/manifest.json').read_text(encoding='utf-8'))
    rows=[row for row in manifest['frame_annotations'] if row['video']=='1694']
    totals={name:{'tp':0,'fp':0,'fn':0} for name in NAMES};results=[];panels=[]
    with HandPoseService() as hands:
        for row in rows:
            image=Image.open(project_path(row['image_path'])).convert('RGB');rgb=np.array(image);width,height=image.size
            started=time.perf_counter();observation=hands.detect(rgb,row['actual_ms']);crops=[];predictions=[]
            for hand in observation['hands']:
                points=hand['landmarks']
                if len(points)!=21 or not all(p['coordinate_valid'] for p in points):continue
                xs=[p['x']*width for p in points];ys=[p['y']*height for p in points]
                cx=(min(xs)+max(xs))/2;cy=(min(ys)+max(ys))/2
                radius=max(64.,1.75*max(max(xs)-min(xs),max(ys)-min(ys)))
                crop=[max(0,math.floor(cx-radius)),max(0,math.floor(cy-radius)),
                      min(width,math.ceil(cx+radius)),min(height,math.ceil(cy+radius))]
                x0,y0,x1,y1=crop;crops.append(crop)
                result=model.predict(source=image.crop(crop),imgsz=320,device='cpu',conf=.25,iou=.7,
                                     max_det=10,verbose=False,save=False)[0]
                for box in result.boxes:
                    left,top,right,bottom=box.xyxy[0].cpu().tolist()
                    predictions.append({'class_id':int(box.cls.item()),'confidence':float(box.conf.item()),
                                        'box':[left+x0,top+y0,right+x0,bottom+y0]})
            kept=[]
            for prediction in sorted(predictions,key=lambda p:p['confidence'],reverse=True):
                if not any(p['class_id']==prediction['class_id'] and iou(p['box'],prediction['box'])>.5 for p in kept):kept.append(prediction)
            # 真值只在推理结束后参与计分，未用于手部区域生成。
            targets=targets_from_json(project_path(row['native_json']));counts=match(kept,targets)
            for name in NAMES:
                for key,value in counts[name].items():totals[name][key]+=value
            results.append({'id':row['id'],'source_image_sha256':row['image_sha256'],'automatic_rois':crops,
                'predictions':kept,'counts':counts,'hands_detected':len(observation['hands']),
                'finger_visibility_verified':False,'elapsed_ms':round((time.perf_counter()-started)*1000,2)})
            draw=ImageDraw.Draw(image)
            for target in targets:draw.rectangle(target['box'],outline='#22bb99',width=1)
            for prediction in kept:
                draw.rectangle(prediction['box'],outline='#ff7e28',width=2)
                draw.text((prediction['box'][0],max(0,prediction['box'][1]-13)),NAMES[prediction['class_id']]+f" {prediction['confidence']:.2f}",
                          fill='#ff7e28',stroke_width=1,stroke_fill='black')
            image.thumbnail((420,350));panel=Image.new('RGB',(430,390),'white');panel.paste(image,(0,30))
            ImageDraw.Draw(panel).text((5,5),row['id']+' auto ROI green=label orange=prediction',fill='black');panels.append(panel)
    for counts in totals.values():
        counts['precision']=counts['tp']/max(1,counts['tp']+counts['fp']);counts['recall']=counts['tp']/max(1,counts['tp']+counts['fn'])
    report={'scope':'same_person_development_input_diagnostic','weights_sha256':training['weights_sha256'],
        'hand_model_sha256':hands.resource['sha256'],'script_sha256':digest(Path(__file__)),
        'uses_ground_truth_for_crop':False,'subject_context_crop_is_manual':True,'confidence_threshold':.25,
        'match_iou_threshold':.5,'roi_minimum_radius_pixels':64,'roi_extent_factor':1.75,
        'totals':totals,'inputs':results,'grip_error_detection_implemented':False,'production_enabled':False}
    atomic_json(ROOT/'data/verification/round3/grip-localizer-autocrop.json',report)
    sheet=Image.new('RGB',(430*len(panels),390),'white')
    for index,panel in enumerate(panels):sheet.paste(panel,(index*430,0))
    sheet.save(OUT/'automatic-crop-predictions.jpg',quality=94)
    print(json.dumps({'automatic_crop_counts':totals,'production_enabled':False},ensure_ascii=False),flush=True)


if __name__=='__main__':evaluate()
