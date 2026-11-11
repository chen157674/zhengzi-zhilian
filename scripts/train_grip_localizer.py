"""明确授权后的本机hand/handle定位候选训练；不训练握持好坏、不发布正式模型。"""
import argparse
import contextlib
import csv
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment,project_path
configure_environment()
os.environ['CUBLAS_WORKSPACE_CONFIG']=':4096:8'
os.environ['WANDB_MODE']='disabled'
from fitness_app.research_extract import atomic_json as _atomic_json
from scripts.prepare_grip_annotations import WORK,inspect as inspect_annotations
from scripts.prepare_xanylabeling import digest

OUT=ROOT/'data/models/grip-localizer-candidate'
STATE=OUT/'training-state.json'
REPORT=ROOT/'data/verification/round3/grip-localizer-training.json'
NAMES=['hand','handle']
TRAIN_VIDEOS={'1575','1693'}
VAL_VIDEOS={'1694'}
EPOCHS=100


def atomic_json(path,value):
    """Windows的短暂读取锁不应中断训练；重试耗尽仍明确抛错。"""
    for attempt in range(8):
        try:return _atomic_json(path,value)
        except PermissionError:
            if attempt==7:raise
            time.sleep(.025*(attempt+1))


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def parameter_digest(model,names):
    checksum=hashlib.sha256()
    for name,value in model.named_parameters():
        if name in names:
            checksum.update(name.encode());checksum.update(value.detach().float().cpu().contiguous().numpy().tobytes())
    return checksum.hexdigest()


def record_training(state):
    path=WORK/'manifest.json';manifest=read(path)
    manifest.setdefault('training_runs',{})['grip-localizer-candidate']={
        'authorization':'用户2026-10-29明确要求开始训练','purpose':'local_hand_handle_localization_development_only',
        'state':state['state'],'completed_epochs':state.get('completed_epochs',0),
        'record_path':STATE.relative_to(ROOT).as_posix(),'production_enabled':False}
    if state['state'] in ('running','completed'):
        manifest['frame_annotation_policy']['training_started']=True
        manifest['frame_annotation_summary']['training_started']=True
    atomic_json(path,manifest)


def prepare_data():
    import yaml
    with contextlib.redirect_stdout(sys.stderr):inspect_annotations()
    manifest=read(WORK/'manifest.json');rows=manifest['localization_exports']
    frames={r['native_json']:r for r in manifest['frame_annotations']}
    sources={r['source_path']:r['source_sha256'] for r in frames.values()}
    for name,expected in sources.items():
        if digest(project_path(name))!=expected:raise ValueError('原视频摘要变化，不能训练')
    train=[];val=[];identities=[]
    for row in rows:
        frame=frames[row['native_json']]
        if not row['visual_crop_reviewed'] or frame['review_status']!='ai_visual_reviewed':raise ValueError('含未复核样本')
        if frame['video'] not in TRAIN_VIDEOS|VAL_VIDEOS:raise ValueError('含未规划来源')
        split='train' if frame['video'] in TRAIN_VIDEOS else 'development_val'
        (train if split=='train' else val).append(project_path(row['image_path']).as_posix())
        identities.append({**row,'video':frame['video'],'split':split})
    if len(train)!=10 or len(val)!=6 or set(train)&set(val):raise ValueError('固定分组数量或隔离不符')
    train_sha={r['source_video_sha256'] for r in identities if r['split']=='train'}
    val_sha={r['source_video_sha256'] for r in identities if r['split']=='development_val'}
    if train_sha&val_sha:raise ValueError('同视频跨分区')
    (OUT/'train.txt').write_text('\n'.join(train)+'\n',encoding='utf-8')
    (OUT/'development-val.txt').write_text('\n'.join(val)+'\n',encoding='utf-8')
    (OUT/'data.yaml').write_text(yaml.safe_dump({'path':OUT.as_posix(),'train':'train.txt',
        'val':'development-val.txt','names':dict(enumerate(NAMES))},sort_keys=False),encoding='utf-8')
    dataset={'schema_version':1,'scope':'same_person_cross_video_development_not_independent_accuracy',
        'authorization':'用户2026-10-29明确授权该本机候选训练','train_videos':sorted(TRAIN_VIDEOS),
        'development_val_videos':sorted(VAL_VIDEOS),'subject_groups_overlap':True,
        'private_upload_allowed':False,'samples':identities}
    atomic_json(OUT/'dataset.json',dataset)
    return manifest,dataset


def iou(a,b):
    intersection=max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]))
    union=(a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-intersection
    return intersection/union if union>0 else 0.


def match(predictions,targets):
    used=set();counts={name:{'tp':0,'fp':0,'fn':0} for name in NAMES}
    for prediction in sorted(predictions,key=lambda p:p['confidence'],reverse=True):
        cls=prediction['class_id'];candidates=[(iou(prediction['box'],target['box']),index)
            for index,target in enumerate(targets) if index not in used and target['class_id']==cls]
        score,index=max(candidates,default=(0.,-1))
        if score>=.5:
            used.add(index);counts[NAMES[cls]]['tp']+=1
        else:counts[NAMES[cls]]['fp']+=1
    for index,target in enumerate(targets):
        if index not in used:counts[NAMES[target['class_id']]]['fn']+=1
    return counts


def targets_from_json(path):
    rows=[]
    for shape in read(path)['shapes']:
        xs,ys=zip(*shape['points'])
        rows.append({'class_id':NAMES.index(shape['label']),'box':[min(xs),min(ys),max(xs),max(ys)]})
    return rows


def evaluate(weight):
    from ultralytics import YOLO
    from PIL import Image,ImageDraw
    model=YOLO(str(weight),task='detect')
    if [model.names[k] for k in sorted(model.names)]!=NAMES:raise ValueError('重载权重类别不符')
    manifest=read(WORK/'manifest.json');inputs=[]
    for patch in manifest['localization_exports']:
        frame=next(r for r in manifest['frame_annotations'] if r['native_json']==patch['native_json'])
        if frame['video'] not in VAL_VIDEOS:continue
        width,height=patch['image_size'];targets=[]
        for line in project_path(patch['label_path']).read_text(encoding='utf-8').splitlines():
            cls,*values=line.split();cx,cy,bw,bh=map(float,values)
            targets.append({'class_id':int(cls),'box':[(cx-bw/2)*width,(cy-bh/2)*height,(cx+bw/2)*width,(cy+bh/2)*height]})
        inputs.append({'id':patch['id'],'scope':'local_patch','image_path':patch['image_path'],'targets':targets,'imgsz':320})
    for frame in manifest['frame_annotations']:
        if frame['video'] in VAL_VIDEOS:
            inputs.append({'id':frame['id'],'scope':'subject_context','image_path':frame['image_path'],
                'targets':targets_from_json(project_path(frame['native_json'])),'imgsz':640})
    totals={scope:{name:{'tp':0,'fp':0,'fn':0} for name in NAMES} for scope in ('local_patch','subject_context')}
    output=[];panels=[]
    for item in inputs:
        start=time.perf_counter()
        prediction=model.predict(source=str(project_path(item['image_path'])),device='cpu',imgsz=item['imgsz'],
            conf=.25,iou=.7,max_det=20,verbose=False,save=False)[0]
        boxes=[{'class_id':int(box.cls.item()),'confidence':float(box.conf.item()),'box':box.xyxy[0].cpu().tolist()}
               for box in prediction.boxes]
        counts=match(boxes,item['targets'])
        for name in NAMES:
            for key,value in counts[name].items():totals[item['scope']][name][key]+=value
        result={**item,'predictions':boxes,'counts':counts,'elapsed_ms':round((time.perf_counter()-start)*1000,2)}
        output.append(result)
        if item['scope']=='subject_context':
            image=Image.open(project_path(item['image_path'])).convert('RGB');draw=ImageDraw.Draw(image)
            for target in item['targets']:draw.rectangle(target['box'],outline='#22bb99',width=1)
            for box in boxes:
                draw.rectangle(box['box'],outline='#ff7e28',width=2)
                draw.text((box['box'][0],max(0,box['box'][1]-13)),NAMES[box['class_id']]+f" {box['confidence']:.2f}",fill='#ff7e28',stroke_width=1,stroke_fill='black')
            image.thumbnail((420,350));panel=Image.new('RGB',(430,390),'white');panel.paste(image,(0,30))
            ImageDraw.Draw(panel).text((5,5),item['id']+' green=label orange=prediction',fill='black');panels.append(panel)
    for scope in totals.values():
        for counts in scope.values():
            counts['precision']=counts['tp']/max(1,counts['tp']+counts['fp'])
            counts['recall']=counts['tp']/max(1,counts['tp']+counts['fn'])
    sheet=Image.new('RGB',(430*len(panels),390),'white')
    for index,panel in enumerate(panels):sheet.paste(panel,(index*430,0))
    sheet.save(OUT/'development-predictions.jpg',quality=94)
    return {'scope':'same_person_development_only','confidence_threshold':.25,'match_iou_threshold':.5,
        'device':'cpu','weights_sha256':digest(weight),'totals':totals,'inputs':output,
        'full_frame_or_subject_detection_pipeline_tested':False,'grip_error_or_body_correction_tested':False}


def train(development=False,resume=False):
    if not development:raise ValueError('本批仅允许显式--development开发候选训练')
    existing=None
    if STATE.exists():
        existing=read(STATE)
        if existing['state']=='completed':
            report=read(REPORT);weight=project_path(report['weights_path'])
            if digest(weight)!=report['weights_sha256']:raise ValueError('候选权重已变化')
            print(json.dumps({'reused':True,'state':'completed','report':str(REPORT)},ensure_ascii=False),flush=True)
            return
        if not resume or existing['state'] not in ('failed','running') or not (OUT/'weights/last.pt').is_file():
            raise ValueError('已有未完成训练记录，只有明确--resume且存在检查点才可继续')
    elif resume:raise ValueError('没有可续训的本次记录')
    if existing is None and OUT.exists() and any(OUT.iterdir()):raise ValueError('已有候选输出，不覆盖')
    OUT.mkdir(exist_ok=True)
    state={'schema_version':1,'state':'preparing','completed_epochs':0,'maximum_epochs':EPOCHS,
        'scope':'development_only','production_enabled':False,'started_at':time.strftime('%Y-%m-%d %H:%M:%S')}
    if existing is not None:
        history=existing.get('prior_attempts',[])+[{key:existing.get(key) for key in (
            'state','started_at','error_type','error','completed_epochs','resumed_checkpoint_sha256','parameter_checks_start')}]
        state.update(started_at=existing['started_at'],prior_attempts=history,
                     resumed_checkpoint_sha256=digest(OUT/'weights/last.pt'))
    atomic_json(STATE,state)
    owned_font=None;cache=WORK/'localization-yolo/labels.cache';cache_existed=cache.exists()
    try:
        import torch
        import yaml
        import ultralytics
        from ultralytics import YOLO,settings
        from ultralytics.utils import LOGGER,USER_CONFIG_DIR
        from ultralytics.utils.torch_utils import init_seeds
        from fitness_app.hand_pose import correction_resource
        settings.update({key:False for key in ('sync','clearml','comet','dvc','mlflow','raytune','tensorboard','wandb')})
        settings.update({'datasets_dir':str(ROOT/'data/datasets'),'weights_dir':str(ROOT/'data/models'),
                         'runs_dir':str(OUT.parent)})
        torch.set_num_threads(2)
        init_seeds(42,deterministic=True)
        if not torch.cuda.is_available():raise RuntimeError('计划使用本机CUDA，但当前不可用；不静默改成长时间CPU训练')
        # Ultralytics的ASCII标签图使用Arial；复用系统字体，不触发默认联网字体下载。
        font=project_path(USER_CONFIG_DIR/'Arial.ttf')
        if not font.exists():
            shutil.copyfile('C:/Windows/Fonts/arial.ttf',font);owned_font=font
        elif resume and f"to '{font}'" in (OUT/'training.log').read_text(encoding='utf-8'):
            owned_font=font  # 首次失败运行的日志证实该字体由本次自动下载。
        manifest,dataset=prepare_data()
        resource,record=correction_resource('yolo')
        architecture_path=Path(ultralytics.__file__).parent/'cfg/models/11/yolo11.yaml'
        architecture=yaml.safe_load(architecture_path.read_text(encoding='utf-8'))
        architecture.update(nc=2,scale='n')
        (OUT/'architecture.yaml').write_text(yaml.safe_dump(architecture,sort_keys=False),encoding='utf-8')
        record_base={'source_weights_path':resource.relative_to(ROOT).as_posix(),'source_weights_sha256':record['sha256'],
            'torch':torch.__version__,'ultralytics':ultralytics.__version__,'gpu':torch.cuda.get_device_name(0),
            'script_sha256':digest(Path(__file__)),'dataset_sha256':digest(OUT/'dataset.json'),
            'source_manifest_sha256':digest(WORK/'manifest.json'),'production_enabled':False,
            'scope':dataset['scope'],'train_samples':10,'development_val_samples':6,
            'independent_subject_validation':False,'api_calls':0}
        record_base['parameter_check_scope']='after_last_resume_to_final_epoch' if resume else 'whole_training'
        if resume:record_base['resumed_checkpoint_sha256']=state['resumed_checkpoint_sha256']
        previous_log=(OUT/'training.log').read_text(encoding='utf-8') if resume else ''
        with (OUT/'training.log').open('a',encoding='utf-8',buffering=1) as log,contextlib.redirect_stdout(log),contextlib.redirect_stderr(log):
            for handler in LOGGER.handlers:
                if isinstance(handler,logging.StreamHandler):handler.setStream(log)
            if resume:
                model=YOLO(str(OUT/'weights/last.pt'),task='detect')
                transfer_match=re.search(r'Transferred (\d+)/(\d+) items from pretrained weights',previous_log)
                if transfer_match is None:raise ValueError('缺少首次迁移证据')
                transferred=int(transfer_match.group(1))
                record_base['epochs_completed_before_resume']=int(model.ckpt['epoch'])+1
            else:
                model=YOLO(str(OUT/'architecture.yaml'),task='detect').load(str(resource))
                source_state=model.ckpt['model'].float().state_dict();target_state=model.model.state_dict()
                transferred=sum(key in source_state and source_state[key].shape==value.shape and torch.equal(source_state[key].cpu(),value.cpu()) for key,value in target_state.items())
            if transferred<100:raise ValueError('迁移的兼容预训练张量不足，未启动训练')
            record_base['transferred_matching_tensors']=transferred
            hashes={};trainable=set();frozen=set()

            def started(trainer):
                trainable.update(name for name,value in trainer.model.named_parameters() if value.requires_grad)
                frozen.update(name for name,value in trainer.model.named_parameters() if not value.requires_grad)
                hashes['trainable_before']=parameter_digest(trainer.model,trainable)
                hashes['frozen_before']=parameter_digest(trainer.model,frozen)
                state['parameter_checks_start']=dict(hashes);atomic_json(STATE,state)

            def epoch_started(trainer):
                if state['state']!='running':
                    state['state']='running';atomic_json(STATE,state);record_training(state)
                    print(json.dumps({'state':'running','epochs':EPOCHS,'transferred_tensors':transferred}),file=sys.__stdout__,flush=True)

            def progress(trainer):
                state.update(completed_epochs=min(trainer.epoch+1,EPOCHS),
                    loss=trainer.label_loss_items(trainer.tloss),
                    development_metrics={key:float(value) for key,value in (trainer.metrics or {}).items()})
                if state['completed_epochs']%5==0 or trainer.stop:atomic_json(STATE,state)
                if state['completed_epochs']%10==0:
                    print(json.dumps({'state':'running','epoch':state['completed_epochs'],'loss':state['loss']}),file=sys.__stdout__,flush=True)

            model.add_callback('on_pretrain_routine_end',started)
            model.add_callback('on_train_epoch_start',epoch_started)
            model.add_callback('on_fit_epoch_end',progress)
            model.train(data=str(OUT/'data.yaml'),project=str(OUT.parent),name=OUT.name,exist_ok=True,
                epochs=EPOCHS,patience=25,batch=4,nbs=4,imgsz=320,device=0,workers=0,seed=42,deterministic=True,
                optimizer='AdamW',lr0=.003,lrf=.05,cos_lr=True,freeze=10,warmup_epochs=3,warmup_bias_lr=0.,
                amp=False,compile=False,mosaic=0.,mixup=0.,copy_paste=0.,degrees=5.,translate=.1,scale=.2,
                fliplr=.5,flipud=0.,close_mosaic=0,cache=False,plots=False,save=True,save_period=-1,verbose=False,resume=resume)
            trainer=model.trainer
            hashes['trainable_after']=parameter_digest(trainer.model,trainable)
            hashes['frozen_after']=parameter_digest(trainer.model,frozen)
            if hashes['trainable_before']==hashes['trainable_after']:raise RuntimeError('可训练参数未发生变化')
            if hashes['frozen_before']!=hashes['frozen_after']:raise RuntimeError('冻结参数被修改')
            steps=[float(value['step'].item()) for value in trainer.optimizer.state.values() if 'step' in value]
            if not steps or min(steps)<=0:raise RuntimeError('缺少实际优化器更新证据')
            with (OUT/'results.csv').open(encoding='utf-8') as stream:history=list(csv.DictReader(stream))
            best=project_path(trainer.best)
            if not best.is_file():raise RuntimeError('未保存best.pt')
            evaluation=evaluate(best)
            report={**record_base,'state':'completed','completed_epochs':len(history),'optimizer_steps_min':min(steps),
                'optimizer_steps_max':max(steps),'parameter_checks':hashes,'trainable_parameter_tensors':len(trainable),
                'frozen_parameter_tensors':len(frozen),'weights_path':best.relative_to(ROOT).as_posix(),
                'weights_sha256':digest(best),'framework_development_metrics':{k:float(v) for k,v in trainer.metrics.items()},
                'evaluation':evaluation,'history_first':history[0],'history_last':history[-1],
                'prior_attempts':state.get('prior_attempts',[])}
            atomic_json(REPORT,report)
        state.update(state='completed',completed_epochs=len(history),weights_path=report['weights_path'],
                     weights_sha256=report['weights_sha256'],report_path=REPORT.relative_to(ROOT).as_posix())
        atomic_json(STATE,state);record_training(state)
        print(json.dumps({'state':'completed','epochs':len(history),'weights':report['weights_path'],
                         'development_counts':evaluation['totals']},ensure_ascii=False),flush=True)
    except Exception as error:
        state.update(state='failed',error_type=type(error).__name__,error=str(error))
        atomic_json(STATE,state);record_training(state)
        raise
    finally:
        if owned_font is not None and owned_font.exists():owned_font.unlink()
        if not cache_existed and cache.exists():cache.unlink()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--development',action='store_true')
    parser.add_argument('--resume',action='store_true');args=parser.parse_args()
    train(args.development,args.resume)
