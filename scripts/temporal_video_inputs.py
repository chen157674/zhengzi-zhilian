"""准备同一训练区间的不同时间窗；只解码原帧，不调用姿态/动作模型。"""
import hashlib,json
import av,numpy as np
from PIL import Image,ImageDraw
from fitness_app.paths import ROOT,project_path
from fitness_app.temporal_training import make_views
from fitness_app.source_partitions import enforce_source_policy
from fitness_app.research_extract import atomic_json
from scripts.encoder_adaptation_data import inputs as previous_inputs,pixels,fingerprint
from scripts.partial_action_training import assert_source_split
from scripts.train_chest_expert import read,sha,OUT

PATH=OUT/'temporal-video-inputs.json'


class SamplingError(ValueError):pass


def resolve_times(row,desired):
    def scan(seek):
        found=[]
        with av.open(str(project_path(row['path']))) as video:
            if seek:video.seek(round(float(desired[0]))*1000)
            for frame in video.decode(video=0):
                if frame.time is None:continue
                stamp=round(float(frame.time)*1000)
                if stamp<desired[len(found)]:continue
                if stamp-desired[len(found)]>100:raise SamplingError('关键帧跳转超出训练采样容差')
                found.append(stamp)
                if len(found)==8:return found
        raise SamplingError('真实训练帧不足8个')
    try:return scan(True)
    except (SamplingError,av.error.FFmpegError):return scan(False)


def verify_sources(rows):
    enforce_source_policy(rows);enforce_source_policy(rows,operation='inference');seen={}
    for row in rows:
        if row['path'] not in seen:seen[row['path']]=sha(project_path(row['path']))
        if seen[row['path']]!=row['source_sha256']:raise ValueError('时间窗原片摘要不同')


def pixel_hash(value):return hashlib.sha256(np.asarray(value,np.float16).tobytes()).hexdigest()


def materialise(plan,config):
    bases=plan['bases'];verify_sources(bases);verified={};centres={r['id']:pixels(r,config,verified) for r in bases};views={}
    for view in plan['views']:
        base=bases[view['base_index']]
        if base['id']!=view['base_id']:raise ValueError('视图与训练片段索引不同')
        row={**base,'actual_ms':view['actual_ms']}
        value=centres[base['id']] if view['actual_ms']==base['actual_ms'] else pixels(row,config,verified)
        if 'pixel_sha256' in view and pixel_hash(value)!=view['pixel_sha256']:raise ValueError('同一训练时间窗的实际像素改变')
        views[view['id']]=value
    return centres,views


def prepare(config):
    if (OUT/'temporal-video-center-control.pt').exists() or (OUT/'temporal-video-multiview.pt').exists():raise ValueError('已有拟合结果，不能重新准备并覆盖输入')
    training,evaluation,routed,parent,expected,observed=previous_inputs();original_y=next(r for r in training if r['action_id']=='cable_y_raise')
    cleaned={**original_y,'id':'youtube-3frdI6LLJ3Y-38000-41000-continuous','start_ms':38000,'end_ms':41000,'replaces_training_id':original_y['id']}
    cleaned['actual_ms']=resolve_times(cleaned,np.linspace(38000,40900,8));training=[cleaned if r['id']==original_y['id'] else r for r in training]
    supplement=read(OUT/'round3-supplement-active-inputs.json');added=[]
    for row in supplement['entries']:
        if row['eligible_training']:
            if row['partition']!='train' or not row.get('review_status','').startswith('classification_window_confirmed'):raise ValueError('新增来源必须是已确认的训练窗口')
            training.append(dict(row));added.append(row['id'])
    if len(added)!=1:raise ValueError('本次固定只加入一个已确认的新面拉窗口')
    assert_source_split(training,evaluation);verify_sources(training);views=make_views(training,resolve_times)
    plan={'schema_version':1,'review_confirmed':False,'scope':'same reviewed class labels; true ordered video frames, no count or posture-error supervision',
        'bases':training,'views':[{k:r[k] for k in ('id','base_id','base_index','view_kind','actual_ms','control_actual_ms')} for r in views],
        'added_training_ids':added,'replaced_training_intervals':[{'original_id':original_y['id'],'original_actual_ms':original_y['actual_ms'],'new_id':cleaned['id'],'reason':'original first frame is an overhead/detail cut; new window uses the same visible cable-Y shot'}],
        'parent_report_sha256':sha(OUT/'domain-adviser-evaluation.json'),
        'sampling':'long intervals >=6400ms: early3200ms / middle3200ms / late3200ms; short intervals keep original8, no padding',
        'control':'same view count/order/weights; every view repeats its base centre pixels',
        'specific_error_ground_truth':False,'new_application_model_inferences':0}
    centres,view_pixels=materialise(plan,config)
    for view in plan['views']:view['pixel_sha256']=pixel_hash(view_pixels[view['id']]);view['control_pixel_sha256']=pixel_hash(centres[view['base_id']])
    selected=[r for r in training if r['action_id'] in {'cable_face_pull','cable_y_raise','seated_cable_row'}]
    sheet=Image.new('RGB',(1530,len(selected)*190),'white');draw=ImageDraw.Draw(sheet);mean=np.asarray(config['image_mean']);std=np.asarray(config['image_std'])
    for i,base in enumerate(selected):
        draw.text((3,i*190+2),base['id'],fill='black');offset=0
        for view in [v for v in plan['views'] if v['base_id']==base['id']]:
            for frame_index in (0,3,7):
                data=view_pixels[view['id']][frame_index].transpose(1,2,0).astype(np.float32);rgb=np.clip((data*std+mean)*255,0,255).round().astype(np.uint8)
                picture=Image.fromarray(rgb);picture.thumbnail((164,150));x=offset*170;y=i*190+37;sheet.paste(picture,(x,y));draw.text((x+3,i*190+20),view['view_kind']+' '+str(view['actual_ms'][frame_index])+'ms',fill='black');offset+=1
    target=OUT/'temporal-video-training-preview.jpg';sheet.save(target,quality=94);plan['preview_path']=target.relative_to(ROOT).as_posix();plan['preview_sha256']=sha(target)
    atomic_json(PATH,plan)
    print(json.dumps({'stage':'temporal_inputs_prepared','bases':len(training),'views':len(views),'reviewed_preview_bases':len(selected),'added':added,'application_model_calls':0,'preview':str(target)}),flush=True)


def load():
    plan=read(PATH)
    if not plan['review_confirmed']:raise ValueError('先查看实际时间窗预览并确认准备记录，不能盲目拟合')
    if sha(project_path(plan['preview_path']))!=plan['preview_sha256']:raise ValueError('预览证据摘要改变')
    training,evaluation,routed,parent,expected,observed=previous_inputs()
    if sha(OUT/'domain-adviser-evaluation.json')!=plan['parent_report_sha256']:raise ValueError('父基准改变')
    assert_source_split(plan['bases'],evaluation);verify_sources(plan['bases'])
    return plan,evaluation,routed,parent,expected,observed
