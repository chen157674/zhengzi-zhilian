"""同一批灰度躯干输入的分类层／视觉层适配对照；不启用生产或读取test。"""
import argparse
from collections import Counter
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment,project_path
configure_environment()
import av
import cv2
import numpy as np
import torch
from fitness_app import deadlift_visual as base
from fitness_app.research_data import load_dataset,DL_FOLDS
from fitness_app.research_extract import atomic_json
from fitness_app.media import display_rgb,source_stamp
from scripts.train_visual_posture import cases,sha
from scripts.train_deadlift_condition import condition_metrics,release_gate
from scripts.partial_action_training import assert_source_split,source_weights

REPORT=ROOT/'data/verification/round3/deadlift-image-adapter-training.json'
DESTINATION=ROOT/'data/models/open-source-correction/deadlift-image-adapter'
CONVS=('Conv_84','Conv_86','Conv_88','Conv_90','Conv_92','Conv_94','Conv_95')
CONFIG={'seed':20261103,'arms':['head-only','body-tune'],'updates':240,'batch_clips':8,
    'head_learning_rate':1e-3,'image_learning_rate':1e-5,'weight_decay':1e-4,
    'dropout':.1,'gradient_clip':1.,'minimum_score':.8,'normalizer_scale_floor':.05,
    'pool_variance_epsilon':1e-6,'trainable_image_convs':list(CONVS),'pool_layer':'464',
    'input':'previous_frozen_eight_gray_torso_patches','mirror':'average_original_and_horizontal_flip_features',
    'validation_schedule':'once_after_fixed_240_updates','head_initialization':'zero_weights_and_bias',
    'scaler':'frozen_backbone_features_from_training_subjects_only','production_enabled':False}


def read(path):return json.loads(project_path(path).read_text(encoding='utf-8'))


def protocol():
    return {'config':CONFIG,'source_index_sha256':sha(ROOT/'data/datasets/index.csv'),
        'old_visual_cache_sha256':sha(base.CACHE),'old_feature_signature':base.feature_signature(),
        'backbone_sha256':sha(ROOT/'data/models/mobilenetv2.onnx'),
        'implementation_sha256':{name:sha(ROOT/name) for name in
            ('scripts/train_back_image_adapter.py','scripts/train_visual_posture.py','scripts/train_deadlift_condition.py',
             'scripts/partial_action_training.py','fitness_app/research_data.py','fitness_app/deadlift_visual.py','fitness_app/media.py')},
        'folds':[list(group) for group in DL_FOLDS],
        'versions':{name:importlib.metadata.version(name) for name in ('torch','onnx2torch','onnx','numpy','safetensors','av')}}


def inputs(rows):
    known={s.key:s for s in load_dataset() if s.action_id=='conventional_deadlift' and s.partition=='development'}
    cached=base.read_cache(base.feature_signature());baseline=read(ROOT/'data/experiments/baseline.json')
    patches=[];records=[]
    for index,case in enumerate(rows):
        sample=known[case['key']];pose,fingerprint=base.input_identity(sample,baseline);old=cached[sample.key]
        if old['fingerprint']!=fingerprint:raise ValueError('原灰度裁片来源已变化')
        record={key:case[key] for key in ('id','source_sha256','source_subject','source_label','condition_fold')}
        record.update(pose_cache=pose.relative_to(ROOT).as_posix(),pose_sha256=sha(pose),fingerprint=fingerprint,reason=old['reason'])
        if old['reason']:
            if old['features'] is not None:raise ValueError('原拒判状态不一致')
            patches.append(None);records.append(record);continue
        wanted=old['summary']['sampled_source_ms']
        if len(wanted)!=8 or len(set(wanted))!=8:raise ValueError('冻结灰度选帧不是八张不同源图')
        if sha(sample.absolute_path)!=sample.sha256:raise ValueError('原灰度媒体摘要变化')
        with np.load(pose,allow_pickle=False) as archive:
            stamps=archive['timestamps_ms'];points=archive['points']
            counts=archive['person_counts'];accepted=archive['identity_accepted']
        gray=[];matrices=[];selected=[]
        with av.open(str(sample.absolute_path),options={'protocol_whitelist':'file'}) as video:
            stream=video.streams.video[0]
            origin=round(float(stream.start_time*stream.time_base)*1000) if stream.start_time is not None else 0
            seen=0
            for i,frame in enumerate(video.decode(stream)):
                stamp=source_stamp(frame,origin)
                if i>=len(stamps) or stamp!=int(stamps[i]):raise ValueError('原片与灰度姿态时间不一致')
                seen+=1
                if stamp not in wanted:continue
                rgb=display_rgb(frame)
                matrix=base.torso_geometry(points[i],rgb.shape[1],rgb.shape[0],int(counts[i]),bool(accepted[i]))
                if matrix is None:raise ValueError('冻结裁片已经失去原可见性')
                patch=cv2.warpAffine(rgb,matrix,(224,224),flags=cv2.INTER_LINEAR,
                    borderMode=cv2.BORDER_CONSTANT,borderValue=(127,127,127))
                gray.append(cv2.cvtColor(patch,cv2.COLOR_RGB2GRAY));matrices.append(matrix.tolist());selected.append(stamp)
        if seen!=len(stamps) or selected!=wanted:raise ValueError('原八帧未完整复现')
        value=np.stack(gray);record.update(sampled_source_ms=selected,affine_matrices=matrices,
            gray_patch_sha256=hashlib.sha256(value.tobytes()).hexdigest(),shape=list(value.shape))
        patches.append(torch.from_numpy(value));records.append(record)
        if (index+1)%25==0:print(json.dumps({'prepared_gray_clips':index+1,'total':len(rows)}),flush=True)
    if len(rows)!=97 or sum(value is not None for value in patches)!=94:
        raise ValueError('必须保留原94可用／97总数及三段拒判')
    return patches,records


def prepare():
    frozen=protocol()
    if REPORT.exists():
        previous=read(REPORT)
        if previous['protocol']!=frozen:raise ValueError('已有视觉层实验协议变化，不覆盖')
        print(json.dumps({'existing_state':previous['state'],'pid':previous.get('pid')}),flush=True);return
    rows=cases();document={'schema_version':1,'state':'preparing','pid':os.getpid(),'protocol':frozen,
        'scope':'publisher_whole_clip_GB_PB_gray_image_adaptation_not_frame_error_truth',
        'production_enabled':False,'test_media_or_features_read':False,'private_media_uploaded':False,
        'paid_api_calls':0,'new_pose_inferences':0,'optimizer_updates':0,'new_supervised_training_performed':False,'arms':{}}
    atomic_json(REPORT,document)
    try:
        _,records=inputs(rows)
        review=read(base.REPORT)['visual_review'];path=project_path(review['path'])
        if not review.get('inspected') or sha(path)!=review['sha256']:raise ValueError('原灰度裁片核对身份不符')
        document.update(state='prepared',inputs=records,visual_review=review,
            note='reuse exact prior crop pixels; source labels remain whole-clip only')
        atomic_json(REPORT,document);print(json.dumps({'state':'prepared','total':97,'usable':94,'original_rejections':3}),flush=True)
    except BaseException as error:
        document.update(state='preparation_failed',error={'type':type(error).__name__,'message':str(error)[:1200]})
        atomic_json(REPORT,document);raise


def encoder():
    import onnx
    from onnx2torch import convert
    path=ROOT/'data/models/mobilenetv2.onnx'
    if sha(path)!=read(ROOT/'data/models/mobilenetv2.json')['sha256']:raise ValueError('图像基座摘要不符')
    graph=onnx.load(str(path),load_external_data=False);del graph.graph.output[:]
    graph.graph.output.append(onnx.helper.make_tensor_value_info('464',onnx.TensorProto.FLOAT,[None,1280,1,1]))
    onnx.checker.check_model(graph)
    model=convert(graph);model.graph.eliminate_dead_code();model.recompile()
    names=[name for name,module in model.named_modules() if isinstance(module,torch.nn.Conv2d)]
    if tuple(names[-7:])!=CONVS:raise ValueError('已核查的后七卷积结构变化')
    return model.eval().to('cuda')


def image_batch(patches):
    gray=patches.reshape(-1,224,224).to('cuda').float()/255
    pixels=gray[:,None].repeat(1,3,1,1)
    mean=pixels.new_tensor([.485,.456,.406])[None,:,None,None]
    std=pixels.new_tensor([.229,.224,.225])[None,:,None,None]
    pixels=(pixels-mean)/std
    return torch.cat((pixels,pixels.flip(-1)),dim=0)


def features(model,patches):
    pixels=image_batch(patches);count=len(pixels)//2
    encoded=model(pixels).flatten(1)
    encoded=((encoded[:count]+encoded[count:])/2).reshape(len(patches),8,1280)
    return torch.cat((encoded.mean(1),(encoded.var(1,unbiased=False)+CONFIG['pool_variance_epsilon']).sqrt()),dim=1)


def digest_state(values):
    digest=hashlib.sha256()
    for name,value in sorted(values.items()):
        digest.update(name.encode());digest.update(value.detach().cpu().contiguous().numpy().tobytes())
    return digest.hexdigest()


def fit():
    from safetensors.torch import save_file,load_file
    if not REPORT.exists():raise ValueError('先准备并核对冻结的灰度输入')
    document=read(REPORT)
    if document['protocol']!=protocol():raise ValueError('拟合前视觉层协议变化')
    if document['state']=='completed':
        print(json.dumps({'existing_completed_experiment':True,'optimizer_updates':document['optimizer_updates']}),flush=True);return
    if document['state']!='prepared' or DESTINATION.exists():raise ValueError('已有未结任务或模型目录，不重复启动／覆盖')
    if not torch.cuda.is_available() or torch.cuda.mem_get_info()[0]<2*2**30:raise RuntimeError('图像适配需要至少2GiB可用CUDA显存')
    rows=cases();patches,records=inputs(rows)
    if records!=document['inputs']:raise ValueError('实际灰度像素与拟合前冻结记录不同')
    torch.manual_seed(CONFIG['seed']);torch.cuda.manual_seed_all(CONFIG['seed']);torch.set_num_threads(2)
    torch.backends.cuda.matmul.allow_tf32=False;torch.backends.cudnn.allow_tf32=False
    model=encoder();model.requires_grad_(False);initial={name:value.detach().cpu().clone() for name,value in model.state_dict().items()}
    valid=[i for i,value in enumerate(patches) if value is not None]
    reference=base.VisualBackbone();actual=[]
    with torch.inference_mode():
        tensor=image_batch(torch.stack([patches[valid[0]]]))
        for image in tensor[:1].cpu().numpy():
            reference.net.setInput(image[None]);actual.append(reference.net.forward('464').reshape(-1).copy())
        converted=model(tensor[:1]).flatten(1).cpu().numpy()
    error=float(np.max(np.abs(converted-np.asarray(actual))))
    if error>.001:raise ValueError('图像基座转换与原OpenCV数值不符')
    frozen=torch.full((len(rows),2560),float('nan'),dtype=torch.float32)
    with torch.inference_mode():
        for start in range(0,len(valid),8):
            selected=valid[start:start+8];frozen[selected]=features(model,torch.stack([patches[i] for i in selected])).cpu()
    if not torch.isfinite(frozen[valid]).all():raise ValueError('冻结图像特征不完整')
    document.update(state='training',pid=os.getpid(),numeric_gate={'real_patch_max_abs_error':error,'passed':True},
        initial_backbone_state_sha256=digest_state(initial),hardware={'gpu':torch.cuda.get_device_name(0),'torch':torch.__version__})
    atomic_json(REPORT,document);began=time.perf_counter();all_predictions={arm:[] for arm in CONFIG['arms']}
    def batch(indices):return torch.stack([patches[int(i)] for i in indices])
    try:
        for fold in range(4):
            train=[i for i,row in enumerate(rows) if row['condition_fold']!=fold]
            evaluation=[i for i,row in enumerate(rows) if row['condition_fold']==fold]
            assert_source_split([{**rows[i],'partition':'train'} for i in train],
                [{**rows[i],'partition':'validation'} for i in evaluation])
            usable=[i for i in train if patches[i] is not None]
            if {rows[i]['source_label'] for i in usable}!={'GB','PB'}:raise ValueError('实际训练输入缺少一种来源条件')
            weights=source_weights([{**rows[i],'action_id':rows[i]['source_label']} for i in usable])
            schedule=np.random.default_rng(CONFIG['seed']+fold).choice(usable,
                size=(CONFIG['updates'],CONFIG['batch_clips']),p=weights/weights.sum())
            schedule_sha=hashlib.sha256(schedule.astype(np.int64).tobytes()).hexdigest()
            mean=frozen[usable].mean(0).to('cuda')
            scale=frozen[usable].std(0,unbiased=False).clamp_min(CONFIG['normalizer_scale_floor']).to('cuda')
            if not torch.isfinite(mean).all() or not torch.isfinite(scale).all():raise ValueError('训练人物归一化无效')
            for arm in CONFIG['arms']:
                model.load_state_dict(initial,strict=True);model.eval();model.requires_grad_(False)
                torch.manual_seed(CONFIG['seed']+fold);torch.cuda.manual_seed_all(CONFIG['seed']+fold)
                body=[]
                if arm=='body-tune':
                    for name in CONVS:
                        module=model.get_submodule(name);module.requires_grad_(True);body.extend(module.parameters())
                head=torch.nn.Sequential(torch.nn.Dropout(CONFIG['dropout']),torch.nn.Linear(2560,2)).to('cuda')
                torch.nn.init.zeros_(head[1].weight);torch.nn.init.zeros_(head[1].bias)
                groups=[{'params':list(head.parameters()),'lr':CONFIG['head_learning_rate']}]
                if body:groups.append({'params':body,'lr':CONFIG['image_learning_rate']})
                optimizer=torch.optim.AdamW(groups,weight_decay=CONFIG['weight_decay'])
                parameters=list(head.parameters())+body;losses=[];history=[];exposure=Counter()
                torch.cuda.reset_peak_memory_stats()
                for update,indices in enumerate(schedule):
                    head.train();optimizer.zero_grad(set_to_none=True)
                    encoded=(features(model,batch(indices)) if body else frozen[indices].to('cuda'))
                    logits=head((encoded-mean)/scale)
                    target=torch.tensor([int(rows[i]['source_label']=='PB') for i in indices],device='cuda')
                    loss=torch.nn.functional.cross_entropy(logits,target)
                    if not torch.isfinite(loss):raise ValueError('图像适配损失非有限值')
                    loss.backward();norm=torch.nn.utils.clip_grad_norm_(parameters,CONFIG['gradient_clip'])
                    if not torch.isfinite(norm):raise ValueError('图像适配梯度非有限值')
                    optimizer.step();losses.append(float(loss.detach()))
                    exposure.update(rows[i]['id'] for i in indices);document['optimizer_updates']+=1
                    document['new_supervised_training_performed']=True
                    del logits,loss,encoded
                    if (update+1)%30==0:
                        progress={'fold':fold,'arm':arm,'updates':update+1,'loss_last_30':float(np.mean(losses[-30:])),
                            'elapsed_seconds':round(time.perf_counter()-began,1),'peak_cuda_mib':round(torch.cuda.max_memory_allocated()/2**20,1)}
                        history.append(progress);document['current_progress']=progress;atomic_json(REPORT,document)
                        print(json.dumps(progress),flush=True)
                head.eval();predictions=[]
                with torch.inference_mode():
                    for index in evaluation:
                        case=rows[index];value={key:case[key] for key in ('id','source_sha256','source_subject','source_label')}
                        if patches[index] is None:
                            value.update(top=None,accepted=None,score=None,reason=records[index]['reason'])
                        else:
                            encoded=features(model,batch([index])) if body else frozen[index:index+1].to('cuda')
                            scores=head((encoded-mean)/scale).softmax(-1)[0].cpu().numpy();top=int(scores.argmax())
                            label=('GB','PB')[top];score=float(scores[top])
                            if not np.isfinite(scores).all():raise ValueError('留出图像预测非有限值')
                            value.update(top=label,accepted=label if score>=CONFIG['minimum_score'] else None,
                                score=score,scores=dict(zip(('GB','PB'),map(float,scores))),reason=None,
                                score_is_calibrated_probability=False)
                        predictions.append(value)
                all_predictions[arm].extend(predictions)
                state={'normalizer.mean':mean.detach().cpu().contiguous(),'normalizer.scale':scale.detach().cpu().contiguous()}
                state.update({'head.'+name:value.detach().cpu().contiguous() for name,value in head.state_dict().items()})
                state.update({'image.'+name:value.detach().cpu().contiguous() for name,value in model.named_parameters() if value.requires_grad})
                if body and all(torch.equal(value,initial[name.removeprefix('image.')]) for name,value in state.items() if name.startswith('image.')):
                    raise ValueError('视觉层没有实际更新')
                if not body and digest_state(model.state_dict())!=document['initial_backbone_state_sha256']:
                    raise ValueError('分类层对照改变了图像基座')
                DESTINATION.mkdir(parents=True,exist_ok=True);target=DESTINATION/(arm+'-fold-'+str(fold)+'.safetensors')
                if target.exists():raise ValueError('该折已有权重，不覆盖')
                temporary=target.with_suffix('.safetensors.part')
                try:
                    save_file(state,str(temporary),metadata={'format':'pt','scope':'whole_clip_GB_PB_only','production_enabled':'false'})
                    temporary.replace(target)
                finally:
                    if temporary.exists():temporary.unlink()
                restored=load_file(str(target),device='cpu')
                if digest_state(restored)!=digest_state(state):raise ValueError('图像适配参数重载不一致')
                result={'fold':fold,'arm':arm,'requested_fit_ids':[rows[i]['id'] for i in train],
                    'actual_fit_ids':[rows[i]['id'] for i in usable],'gradient_exposure_counts':dict(exposure),
                    'evaluation_ids':[rows[i]['id'] for i in evaluation],
                    'training_subjects':sorted({rows[i]['source_subject'] for i in usable}),
                    'validation_subjects':sorted({rows[i]['source_subject'] for i in evaluation}),
                    'sampling_schedule_sha256':schedule_sha,'normalizer_fit_ids':[rows[i]['id'] for i in usable],
                    'optimizer_updates':len(losses),'trainable_parameters':sum(p.numel() for p in parameters),
                    'loss_first_30':float(np.mean(losses[:30])),'loss_last_30':float(np.mean(losses[-30:])),
                    'predictions':predictions,'metrics':condition_metrics(predictions),'history':history,
                    'weights_path':target.relative_to(ROOT).as_posix(),'weights_sha256':sha(target),
                    'parameter_reload_verified':True,'production_enabled':False}
                document['arms'].setdefault(arm,{'folds':[]})['folds'].append(result)
                atomic_json(REPORT,document);print(json.dumps({'finished_fold':fold,'arm':arm,'metrics':result['metrics']}),flush=True)
                del optimizer,head
        for arm in CONFIG['arms']:
            values=all_predictions[arm]
            if len(values)!=97 or {v['id'] for v in values}!={r['id'] for r in rows}:raise ValueError('图像适配折外分母不完整')
            metrics=condition_metrics(values);document['arms'][arm].update(metrics=metrics,
                gate=release_gate(metrics,document['arms'][arm]['folds']),predictions=values)
        for case in rows:
            if sha(project_path(case['path']))!=case['source_sha256']:raise ValueError('训练期间原媒体变化')
        if protocol()!=document['protocol']:raise ValueError('训练期间视觉协议或基座变化')
        document.update(state='completed',elapsed_seconds=round(time.perf_counter()-began,1),
            new_supervised_training_performed=True,limitations=[
                '作者整片条件标签不能提供逐帧圆背起点或脊柱曲度。',
                '固定人物折仍为历史开发分区，不是全新外部盲测。',
                '未读取保留test，开发指标不代表发布或真实相机验收。'])
        document.pop('current_progress',None);atomic_json(REPORT,document)
        print(json.dumps({'state':'completed','optimizer_updates':document['optimizer_updates'],
            'arms':{arm:{key:document['arms'][arm][key] for key in ('metrics','gate')} for arm in CONFIG['arms']}}),flush=True)
    except BaseException as error:
        document.update(state='interrupted' if isinstance(error,KeyboardInterrupt) else 'failed',
            error={'type':type(error).__name__,'message':str(error)[:1500]})
        atomic_json(REPORT,document);raise
    finally:
        if torch.cuda.is_available():torch.cuda.empty_cache()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--prepare-only',action='store_true')
    if parser.parse_args().prepare_only:prepare()
    else:fit()
