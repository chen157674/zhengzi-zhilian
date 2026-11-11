"""准备本机握持定位小批；候选与视觉复核分开，不训练、不调用外部API。"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment,project_path
configure_environment()
from fitness_app.research_extract import atomic_json
from scripts.prepare_xanylabeling import digest

WORK=ROOT/'data/annotation/x-anylabeling'
# 裁剪经原视频概览人工选定，保留本人双手与握持部位，排除镜中/背景人物。
SAMPLES=[('1693',4967,(350,80,980,600)),('1693',14933,(350,80,980,600)),
         ('1694',6667,(350,110,1000,610)),('1694',13267,(350,110,1000,610)),
         ('1694',19933,(350,110,1000,610)),('1575',7333,(300,160,940,550)),
         ('1575',14600,(300,160,940,550)),('1575',21900,(300,160,940,550))]


def prepare():
    import av
    from PIL import Image,ImageDraw
    from fitness_app.hand_pose import HandPoseService
    manifest_path=WORK/'manifest.json'
    manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
    if manifest.get('frame_annotations'):
        if len(manifest['frame_annotations'])!=len(SAMPLES):raise ValueError('已有图像批次不完整，不重建')
        for row in manifest['frame_annotations']:
            if digest(project_path(row['image_path']))!=row['image_sha256']:raise ValueError('已有标注图摘要变化')
        print('复用已有8图，不覆盖框或复核结果。',flush=True)
        return
    folder=project_path(WORK/'frames')
    if folder.exists() and any(folder.iterdir()):raise ValueError('已有非空图像目录，不覆盖')
    folder.mkdir(exist_ok=True)
    assets=json.loads((ROOT/'data/presentation-assets.json').read_text(encoding='utf-8'))
    cache=json.loads((ROOT/'data/verification/round3/correction-input-review.json').read_text(encoding='utf-8'))
    cached={(v['number'],s['t']):s for v in cache['videos'] for s in v['samples']}
    checked_sources=set();rows=[]
    with HandPoseService() as hands:
        for number,stamp,crop in SAMPLES:
            asset=assets['demos']['user-'+number];video=project_path(asset['video']['path'])
            if number not in checked_sources:
                if digest(video)!=asset['video']['sha256']:raise ValueError('源视频SHA不匹配')
                checked_sources.add(number)
            with av.open(str(video)) as container:
                stream=container.streams.video[0]
                container.seek(int(stamp/1000/stream.time_base),stream=stream)
                frame=next(f for f in container.decode(stream) if float(f.pts*stream.time_base)*1000>=stamp-1)
                actual_ms=round(float(frame.pts*stream.time_base)*1000)
                rgb=frame.to_ndarray(format='rgb24')
            h,w=rgb.shape[:2];x0,y0,x1,y1=crop
            if not 0<=x0<x1<=w or not 0<=y0<y1<=h:raise ValueError('裁剪越界')
            name=f'{number}_{actual_ms:06d}'
            target=folder/(name+'.jpg');image=Image.fromarray(rgb[y0:y1,x0:x1]);image.save(target,quality=96,subsampling=0)
            observation=hands.detect(rgb,actual_ms,crop_pixels=crop)
            candidates=[]
            for hand in observation['hands']:
                valid=[p for p in hand['landmarks'] if p['coordinate_valid']]
                if len(valid)!=21:continue
                xs=[p['x']*w-x0 for p in valid];ys=[p['y']*h-y0 for p in valid]
                pad=max(5.,.12*max(max(xs)-min(xs),max(ys)-min(ys)))
                box=[max(0,min(xs)-pad),max(0,min(ys)-pad),min(x1-x0,max(xs)+pad),min(y1-y0,max(ys)+pad)]
                candidates.append({'label':'hand','box':[round(v,2) for v in box],
                                   'source':'local_mediapipe_landmarks','reviewed':False})
            previous=cached.get((number,stamp))
            row={'id':name,'video':number,'action_id':asset['action_id'],'source_path':asset['video']['path'],
                 'source_sha256':asset['video']['sha256'],'requested_ms':stamp,'actual_ms':actual_ms,
                 'source_size':[w,h],'crop_xyxy':list(crop),'image_path':target.relative_to(ROOT).as_posix(),
                 'image_sha256':digest(target),'image_size':list(image.size),'candidates':candidates,
                 'hand_model_sha256':hands.resource['sha256'],'body_candidate_cache':
                    {'path':'data/verification/round3/correction-input-review.json','timestamp_ms':stamp,
                     'people':previous['yolo_people']} if previous else None,
                 'scope':'development_only','subject_group':'user_local_demos_same_group',
                 'review_status':'pending','training_eligible':False}
            rows.append(row)
            print(json.dumps({'image':name,'actual_ms':actual_ms,'hand_candidates':len(candidates)},ensure_ascii=False),flush=True)
    manifest['frame_annotations']=rows
    manifest['frame_annotation_classes']=['hand','handle']
    manifest['frame_annotation_policy']={'anatomical_side_confirmed':False,'classes_are_localization_only':True,
        'closed_hand_is_not_correctness_ground_truth':True,'independent_split_ready':False,'training_started':False}
    atomic_json(manifest_path,manifest)
    overview(rows)


def overview(rows=None):
    from PIL import Image,ImageDraw
    if rows is None:rows=json.loads((WORK/'manifest.json').read_text(encoding='utf-8'))['frame_annotations']
    sheet=Image.new('RGB',(1320,4*550),'#eeeeee');draw=ImageDraw.Draw(sheet)
    for index,row in enumerate(rows):
        image=Image.open(project_path(row['image_path'])).convert('RGB')
        painter=ImageDraw.Draw(image)
        for candidate in row['candidates']:painter.rectangle(candidate['box'],outline='#ffa500',width=2)
        x=index%2*660;y=index//2*550
        sheet.paste(image,(x,y+25));draw.text((x+5,y+5),row['id']+' candidates',fill='black')
    sheet.save(WORK/'grip-frame-candidates.jpg',quality=93)


def inspect():
    from PIL import Image
    manifest=json.loads((WORK/'manifest.json').read_text(encoding='utf-8'))
    counts={'images':0,'reviewed':0,'boxes':0,'local_patches':0,'yolo_rows':0}
    for row in manifest['frame_annotations']:
        image=project_path(row['image_path'])
        if digest(image)!=row['image_sha256']:raise ValueError('图像摘要变化')
        with Image.open(image) as picture:
            if list(picture.size)!=row['image_size']:raise ValueError('图像尺寸变化')
        counts['images']+=1
        annotation=image.with_suffix('.json')
        if not annotation.exists():continue
        data=json.loads(annotation.read_text(encoding='utf-8'))
        width,height=row['image_size']
        if data['imageWidth']!=width or data['imageHeight']!=height or data['imagePath']!=image.name:raise ValueError('原生JSON关联不符')
        for shape in data['shapes']:
            if shape['shape_type']!='rectangle' or shape['label'] not in ('hand','handle'):raise ValueError('框类别不符')
            xs,ys=zip(*shape['points'])
            if not 0<=min(xs)<max(xs)<=width or not 0<=min(ys)<max(ys)<=height:raise ValueError('框越界')
            counts['boxes']+=1
        if row['review_status']=='ai_visual_reviewed':
            if row.get('annotation_sha256')!=digest(annotation):raise ValueError('复核后标注变化，需重新复核')
            counts['reviewed']+=1
    for patch in manifest.get('localization_exports',[]):
        import math
        image=project_path(patch['image_path']);label=project_path(patch['label_path'])
        if digest(image)!=patch['image_sha256'] or digest(label)!=patch['label_sha256']:raise ValueError('局部导出摘要变化')
        with Image.open(image) as picture:
            if list(picture.size)!=patch['image_size']:raise ValueError('导出尺寸不符')
        lines=label.read_text(encoding='utf-8').splitlines()
        if len(lines)!=2:raise ValueError('每个局部样本应有手、把手两个框')
        width,height=patch['image_size'];x0,y0,_,_=patch['crop_in_annotation_image']
        native=project_path(patch['native_json'])
        if digest(native)!=patch['annotation_sha256'] or digest(project_path(patch['source_annotation_image']))!=patch['source_image_sha256']:raise ValueError('导出的原标注或原图已变化')
        source=json.loads(native.read_text(encoding='utf-8'))
        group=[s for s in source['shapes'] if s['group_id']==patch['group_id']]
        for line,shape in zip(lines,group):
            cls,cx,cy,bw,bh=line.split();values=list(map(float,(cx,cy,bw,bh)))
            if cls!=str(('hand','handle').index(shape['label'])) or not all(math.isfinite(v) and 0<v<=1 for v in values):raise ValueError('YOLO格式无效')
            cx,cy,bw,bh=values
            recovered=[(cx-bw/2)*width+x0,(cy-bh/2)*height+y0,(cx+bw/2)*width+x0,(cy+bh/2)*height+y0]
            xs,ys=zip(*shape['points']);expected=[min(xs),min(ys),max(xs),max(ys)]
            if max(abs(a-b) for a,b in zip(recovered,expected))>.001:raise ValueError('导出框无法回到原图坐标')
            counts['yolo_rows']+=1
        counts['local_patches']+=1
    print(json.dumps({**counts,'training_started':bool(manifest['frame_annotation_policy'].get('training_started')),
                      'independent_accuracy_claimed':False},ensure_ascii=False),flush=True)


def export():
    from PIL import Image,ImageDraw
    import math
    path=WORK/'manifest.json';manifest=json.loads(path.read_text(encoding='utf-8'))
    if manifest.get('localization_exports'):
        inspect()
        return
    # 在创建输出前检查全部输入，拒绝把待复核或已改变的标注混入导出。
    inspect()
    if len(manifest['frame_annotations'])!=len(SAMPLES):raise ValueError('图像批次不完整')
    for row in manifest['frame_annotations']:
        native=project_path(row['native_json'])
        if row['review_status']!='ai_visual_reviewed' or digest(native)!=row['annotation_sha256']:raise ValueError('图像尚未复核或复核后发生变化')
        shapes=json.loads(native.read_text(encoding='utf-8'))['shapes']
        if len(shapes)!=4 or any([s['label'] for s in shapes if s['group_id']==gid]!=['hand','handle'] for gid in (1,2)):raise ValueError('手与把手配对不完整')
    folder=project_path(WORK/'localization-yolo')
    if folder.exists() and any(folder.iterdir()):raise ValueError('已有非空导出目录，不覆盖')
    images=folder/'images';labels=folder/'labels'
    images.mkdir(parents=True,exist_ok=True);labels.mkdir()
    patches=[]
    for row in manifest['frame_annotations']:
        image=project_path(row['image_path']);annotation=image.with_suffix('.json')
        if row['review_status']!='ai_visual_reviewed' or digest(annotation)!=row['annotation_sha256']:raise ValueError('图像尚未复核或复核后发生变化')
        data=json.loads(annotation.read_text(encoding='utf-8'));width,height=row['image_size']
        for gid in (1,2):
            shapes=[s for s in data['shapes'] if s['group_id']==gid]
            if [s['label'] for s in shapes]!=['hand','handle']:raise ValueError('手与把手配对不完整')
            points=[p for s in shapes for p in s['points']];xs,ys=zip(*points)
            crop=[max(0,math.floor(min(xs))-16),max(0,math.floor(min(ys))-16),min(width,math.ceil(max(xs))+16),min(height,math.ceil(max(ys))+16)]
            x0,y0,x1,y1=crop;pw,ph=x1-x0,y1-y0;name=row['id']+f'_pair{gid}'
            target=images/(name+'.png');label=labels/(name+'.txt');lines=[]
            with Image.open(image) as source:source.crop(crop).save(target)
            for shape in shapes:
                sx,sy=zip(*shape['points']);left,top,right,bottom=min(sx)-x0,min(sy)-y0,max(sx)-x0,max(sy)-y0
                cls=('hand','handle').index(shape['label'])
                lines.append(f'{cls} {(left+right)/2/pw:.8f} {(top+bottom)/2/ph:.8f} {(right-left)/pw:.8f} {(bottom-top)/ph:.8f}')
            label.write_text('\n'.join(lines)+'\n',encoding='utf-8')
            patches.append({'id':name,'source_annotation_image':row['image_path'],'source_image_sha256':row['image_sha256'],
                'native_json':row['native_json'],'annotation_sha256':row['annotation_sha256'],'group_id':gid,
                'source_video_sha256':row['source_sha256'],'actual_ms':row['actual_ms'],'crop_in_annotation_image':crop,
                'image_size':[pw,ph],'image_path':target.relative_to(ROOT).as_posix(),'image_sha256':digest(target),
                'label_path':label.relative_to(ROOT).as_posix(),'label_sha256':digest(label),
                'scope':'development_only','subject_group':row['subject_group'],'training_eligible':False,
                'visual_crop_reviewed':False})
    (folder/'classes.txt').write_text('hand\nhandle\n',encoding='utf-8')
    manifest['localization_exports']=patches;atomic_json(path,manifest)
    sheet=Image.new('RGB',(800,4*180),'#eeeeee');draw=ImageDraw.Draw(sheet)
    for index,patch in enumerate(patches):
        im=Image.open(project_path(patch['image_path'])).convert('RGB');painter=ImageDraw.Draw(im)
        for line in project_path(patch['label_path']).read_text(encoding='utf-8').splitlines():
            cls,*values=line.split();cx,cy,bw,bh=map(float,values);iw,ih=im.size
            painter.rectangle(((cx-bw/2)*iw,(cy-bh/2)*ih,(cx+bw/2)*iw,(cy+bh/2)*ih),outline='#00b878' if cls=='0' else '#ec9633',width=2)
        im.thumbnail((195,150));x=index%4*200;y=index//4*180;sheet.paste(im,(x,y+23));draw.text((x+3,y+3),patch['id'],fill='black')
    sheet.save(WORK/'grip-localization-export.jpg',quality=95)
    inspect()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);actions=parser.add_mutually_exclusive_group()
    actions.add_argument('--inspect',action='store_true');actions.add_argument('--export',action='store_true');args=parser.parse_args()
    inspect() if args.inspect else export() if args.export else prepare()
