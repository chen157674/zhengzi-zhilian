"""本地完整硬拉来源条件QLoRA；固定人物折、真实基线及全部拒判分母。"""
import argparse
from collections import Counter
from functools import lru_cache
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import random
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment,project_path
configure_environment()
os.environ['HF_HUB_OFFLINE']='1'
os.environ['TRANSFORMERS_OFFLINE']='1'
os.environ['HF_HUB_DISABLE_PROGRESS_BARS']='1'
import av
import numpy as np
import torch
from fitness_app.visual_posture import VisualBackConditionCandidate,CONFIG,PROMPT
from fitness_app.media import display_rgb,source_stamp
from fitness_app.research_data import load_dataset,DL_FOLDS
from fitness_app.research_extract import atomic_json
from scripts.train_deadlift_condition import cases_for,condition_metrics,release_gate
from scripts.partial_action_training import source_weights,assert_source_split

OUT=ROOT/'data/verification/round3'
REPORT=OUT/'deadlift-vlm-training.json'
DESTINATION=ROOT/'data/models/open-source-correction/deadlift-vlm'
SEED=20261103
TRAINING={'seed':SEED,'micro_steps_per_fold':256,'gradient_accumulation':4,'learning_rate':1e-4,
    'weight_decay':.01,'gradient_clip':1.,'lora_r':8,'lora_alpha':16,'lora_dropout':.05,
    'last_attention_layers':8,'target_modules':['q_proj','k_proj','v_proj','o_proj'],
    'validation_schedule':'once_after_all_fixed_steps','threshold_tuning':False}


def sha(path):
    with project_path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def read(path):return json.loads(project_path(path).read_text(encoding='utf-8'))


def cases():
    entries=read(ROOT/'data/verification/step-22/semantic-video-features.json')['entries']
    samples=load_dataset();rows=cases_for(entries,samples)
    expected={s.sha256 for s in samples if s.dataset_id=='w5prmmxyt9' and s.partition=='development'}
    if len(rows)!=97 or {r['source_sha256'] for r in rows}!=expected:
        raise ValueError('必须保留全部97段开发原片，不能挑选可用结果')
    return rows


def identity(model,rows):
    return {'base_repo':model.base.info['repo'],'base_revision':model.base.info['revision'],
        'base_weight_sha256':{n:v['sha256'] for n,v in model.base.info['files'].items() if n.endswith('.safetensors')},
        'prompt_sha256':hashlib.sha256(PROMPT.encode()).hexdigest(),'input_config':CONFIG,'training_config':TRAINING,
        'source_index_sha256':sha(ROOT/'data/datasets/index.csv'),
        'development_index_sha256':sha(ROOT/'data/verification/step-22/semantic-video-features.json'),
        'implementation_sha256':{name:sha(ROOT/name) for name in
            ('scripts/train_visual_posture.py','fitness_app/visual_posture.py','fitness_app/video_vlm.py','fitness_app/media.py')},
        'inputs':[{key:r[key] for key in ('id','path','source_sha256','source_subject','source_label','condition_fold')} for r in rows],
        'folds':[list(group) for group in DL_FOLDS],
        'versions':{name:importlib.metadata.version(name) for name in ('torch','transformers','peft','bitsandbytes','av','safetensors')}}


def full_rep_frames(case):
    """按实际解码序号选完整原片的八帧，不复制旧居中窗口或补造末帧。"""
    path=project_path(case['path'])
    if sha(path)!=case['source_sha256']:raise ValueError('训练原片摘要变化')
    times=[]
    with av.open(str(path),options={'protocol_whitelist':'file'}) as video:
        stream=video.streams.video[0]
        origin=round(float(stream.start_time*stream.time_base)*1000) if stream.start_time is not None else 0
        for frame in video.decode(stream):times.append(source_stamp(frame,origin))
    if len(times)<CONFIG['frames'] or any(b<=a for a,b in zip(times,times[1:])):
        raise ValueError('原视频真实源时间不足或不递增')
    indices=np.linspace(0,len(times)-1,CONFIG['frames']).round().astype(int).tolist()
    wanted=set(indices);images=[]
    with av.open(str(path),options={'protocol_whitelist':'file'}) as video:
        for index,frame in enumerate(video.decode(video.streams.video[0])):
            if index in wanted:images.append(display_rgb(frame))
            if index==indices[-1]:break
    selected=[int(times[index]) for index in indices]
    if len(images)!=len(indices):raise ValueError('两次解码帧数不一致')
    return images,selected,{'decoded_frames':len(times),'selected_frame_indices':indices,
        'source_ms':selected,'actual_source_end_ms':int(times[-1]),'sampling':'uniform_actual_frame_indices_over_complete_original_clip'}


class Inputs:
    def __init__(self,model,rows,expected=None):
        self.model=model;self.rows={r['id']:r for r in rows};self.records={}
        self.expected={r['id']:r for r in expected} if expected else None

    @lru_cache(maxsize=12)
    def prepared(self,id):
        case=self.rows[id];images,times,metadata=full_rep_frames(case)
        prepared=self.model.prepare(images,times);digest=hashlib.sha256()
        for key,value in sorted(prepared.items()):
            digest.update(key.encode());digest.update(str(value.dtype).encode())
            digest.update(np.asarray(value.shape,dtype=np.int64).tobytes())
            digest.update(value.cpu().numpy().tobytes())
        record={'id':id,'source_sha256':case['source_sha256'],**metadata,
            'frame_rgb_sha256':[hashlib.sha256(image.tobytes()).hexdigest() for image in images],
            'image_sizes':[[image.shape[1],image.shape[0]] for image in images],
            'prepared_tensor_sha256':digest.hexdigest(),'video_grid_thw':prepared['video_grid_thw'].tolist()}
        if self.expected is not None and self.expected[id]!=record:
            raise ValueError('拟合前冻结的实际画面／处理器张量变化')
        self.records[id]=record
        return prepared


def preview(rows):
    from PIL import Image,ImageDraw,ImageFont,ImageOps
    font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',16)
    choices=[next(r for r in rows if r['source_subject']==subject and r['source_label']==label)
        for subject in ('S01','S06') for label in ('GB','PB')]
    image=Image.new('RGB',(960,1220),'white');draw=ImageDraw.Draw(image)
    draw.text((15,10),'完整原片训练输入核对：GB／PB是整次来源标签，展示帧没有独立错误真值',font=font,fill='black')
    for index,case in enumerate(choices):
        frames,times,_=full_rep_frames(case);y=45+index*280
        for column,frame_index in enumerate((2,5)):
            frame=ImageOps.contain(Image.fromarray(frames[frame_index]),(460,235))
            image.paste(frame,(10+column*480+(460-frame.width)//2,y))
            draw.text((15+column*480,y+238),case['source_subject']+' '+case['source_label']+' · '+str(times[frame_index])+'ms',font=font,fill='black')
    draw.text((15,1180),'Ivan Conanta / Gloria Virginia · MyDeadlift v1 · CC BY 4.0 · DOI 10.17632/w5prmmxyt9.1',font=font,fill='black')
    path=OUT/'deadlift-vlm-inputs.jpg';image.save(path,quality=91)
    return {'path':path.relative_to(ROOT).as_posix(),'sha256':sha(path),'ids':[r['id'] for r in choices],
        'scope':'decoded_training_pixels_and_clip_labels_only_not_expert_frame_truth'}


def prepare():
    rows=cases();model=VisualBackConditionCandidate();frozen=identity(model,rows)
    if REPORT.exists():
        existing=read(REPORT)
        if existing['identity']!=frozen:raise ValueError('既有实验协议变化，先核查，不覆盖')
        print(json.dumps({'existing_state':existing['state'],'pid':existing.get('pid')}),flush=True);return
    report={'schema_version':1,'state':'preparing','pid':os.getpid(),'identity':frozen,
        'runtime_enabled':False,'new_supervised_training_performed':False,'optimizer_updates':0,
        'scope':'publisher_whole_rep_GB_PB_visual_instruction_adaptation_not_frame_localization',
        'private_media_uploaded':False,'paid_api_calls':0,'test_media_or_features_read':False,'folds':[]}
    atomic_json(REPORT,report)
    try:
        inputs=Inputs(model,rows)
        for index,row in enumerate(rows):
            inputs.prepared(row['id'])
            if (index+1)%20==0:print(json.dumps({'prepared':index+1,'total':len(rows)}),flush=True)
        report.update(state='prepared',prepared_inputs=[inputs.records[r['id']] for r in rows],preview=preview(rows))
        atomic_json(REPORT,report);print(json.dumps({'state':'prepared','clips':len(rows),'preview':report['preview']}),flush=True)
    except BaseException as error:
        report.update(state='preparation_failed',error={'type':type(error).__name__,'message':str(error)[:1000]})
        atomic_json(REPORT,report);raise


def state_digest(state):
    digest=hashlib.sha256()
    for key,value in sorted(state.items()):
        digest.update(key.encode());digest.update(value.detach().float().cpu().contiguous().numpy().tobytes())
    return digest.hexdigest()


def fit():
    from peft import LoraConfig,get_peft_model,prepare_model_for_kbit_training,get_peft_model_state_dict,set_peft_model_state_dict
    from safetensors.torch import save_file,load_file
    rows=cases();model=VisualBackConditionCandidate()
    if not REPORT.exists():raise ValueError('先执行--prepare-only并核对实际原图')
    report=read(REPORT)
    if report['identity']!=identity(model,rows):raise ValueError('拟合前协议或来源发生变化')
    if report['state']=='completed':
        print(json.dumps({'cached_completed_training':True,'optimizer_updates':report['optimizer_updates']}),flush=True);return
    if report['state']!='prepared':raise ValueError('已有任务未终结或失败，先核验所记PID与原因，不重复启动')
    if DESTINATION.exists():raise ValueError('候选目录已存在，不能覆盖其他训练')
    if not torch.cuda.is_available() or torch.cuda.mem_get_info()[0]<4*2**30:raise RuntimeError('当前可用CUDA显存不足4GiB')
    random.seed(SEED);np.random.seed(SEED);torch.manual_seed(SEED);torch.cuda.manual_seed_all(SEED);torch.set_num_threads(4)
    model.load();net=prepare_model_for_kbit_training(model.base.model,use_gradient_checkpointing=True,
        gradient_checkpointing_kwargs={'use_reentrant':False})
    layers=net.config.text_config.num_hidden_layers
    config=LoraConfig(r=TRAINING['lora_r'],lora_alpha=TRAINING['lora_alpha'],lora_dropout=TRAINING['lora_dropout'],
        target_modules=TRAINING['target_modules'],layers_to_transform=list(range(layers-TRAINING['last_attention_layers'],layers)),
        layers_pattern='layers',bias='none',task_type='CAUSAL_LM')
    net=get_peft_model(net,config);net.config.use_cache=False;model.base.model=net
    trainable=[p for p in net.parameters() if p.requires_grad]
    if not trainable or any(p.requires_grad and 'lora_' not in name for name,p in net.named_parameters()):
        raise ValueError('允许训练的仅为指定LoRA参数')
    initial={key:value.detach().cpu().clone() for key,value in get_peft_model_state_dict(net).items()}
    initial_sha=state_digest(initial);inputs=Inputs(model,rows,report['prepared_inputs'])
    report.update(state='training',pid=os.getpid(),trainable_parameters=sum(p.numel() for p in trainable),
        initial_adapter_sha256=initial_sha,hardware={'gpu':torch.cuda.get_device_name(0),'torch':torch.__version__})
    atomic_json(REPORT,report);began=time.perf_counter()
    def evaluate(cases):
        predictions=[]
        for case in cases:
            value=model.predict_prepared(inputs.prepared(case['id']))
            predictions.append({key:case[key] for key in ('id','source_sha256','source_subject','source_label')}|value)
        return predictions
    try:
        baseline=evaluate(rows);report['baseline']={'origin':'unadapted_quantized_base_zero_LoRA_B',
            'predictions':baseline,'metrics':condition_metrics(baseline)}
        atomic_json(REPORT,report);print(json.dumps({'baseline':report['baseline']['metrics']}),flush=True)
        all_predictions=[]
        for fold in range(4):
            training=[{**r,'partition':'train'} for r in rows if r['condition_fold']!=fold]
            validation=[{**r,'partition':'validation'} for r in rows if r['condition_fold']==fold]
            assert_source_split(training,validation)
            loaded=set_peft_model_state_dict(net,initial)
            if loaded.unexpected_keys or any('lora_' in key for key in loaded.missing_keys):
                raise ValueError('LoRA初始化重置不完整')
            if state_digest(get_peft_model_state_dict(net))!=initial_sha:raise ValueError('各折初始参数不同')
            torch.manual_seed(SEED+fold);torch.cuda.manual_seed_all(SEED+fold)
            rng=np.random.default_rng(SEED+fold)
            weights=source_weights([{**r,'action_id':r['source_label']} for r in training]);probabilities=weights/weights.sum()
            optimizer=torch.optim.AdamW(trainable,lr=TRAINING['learning_rate'],weight_decay=TRAINING['weight_decay'])
            optimizer.zero_grad(set_to_none=True);accumulation=TRAINING['gradient_accumulation'];losses=[];exposure=Counter();updates=0
            torch.cuda.reset_peak_memory_stats();history=[]
            for step in range(TRAINING['micro_steps_per_fold']):
                case=training[int(rng.choice(len(training),p=probabilities))];exposure[case['id']]+=1
                prepared=inputs.prepared(case['id']);batch={key:value.to('cuda') for key,value in prepared.items()}
                target=model.choice_ids[0 if case['source_label']=='GB' else 1][None]
                net.train()
                with torch.autocast('cuda',dtype=torch.bfloat16):
                    logits=net(**batch,logits_to_keep=1,use_cache=False).logits[:,-1].float()
                    loss=torch.nn.functional.cross_entropy(logits,target)
                if not torch.isfinite(loss):raise ValueError('视觉纠错训练损失非有限值')
                (loss/accumulation).backward();losses.append(float(loss.detach()))
                if (step+1)%accumulation==0:
                    norm=torch.nn.utils.clip_grad_norm_(trainable,TRAINING['gradient_clip'])
                    if not torch.isfinite(norm):raise ValueError('视觉纠错梯度非有限值')
                    optimizer.step();optimizer.zero_grad(set_to_none=True);updates+=1
                    report['optimizer_updates']+=1;report['new_supervised_training_performed']=True
                del batch,logits,loss
                if (step+1)%32==0:
                    progress={'fold':fold,'micro_step':step+1,'optimizer_updates':updates,'loss_last_32':float(np.mean(losses[-32:])),
                        'elapsed_seconds':round(time.perf_counter()-began,1),'peak_cuda_mib':round(torch.cuda.max_memory_allocated()/2**20,1)}
                    history.append(progress);report['current_progress']=progress;atomic_json(REPORT,report)
                    print(json.dumps(progress),flush=True)
            predictions=evaluate(validation);all_predictions.extend(predictions)
            state={key:value.detach().cpu().contiguous() for key,value in get_peft_model_state_dict(net).items()}
            if state_digest(state)==initial_sha or updates!=TRAINING['micro_steps_per_fold']//accumulation:
                raise ValueError('没有预定的真实参数更新')
            folder=project_path(DESTINATION/('fold-'+str(fold)));folder.mkdir(parents=True,exist_ok=False)
            config.save_pretrained(folder);path=folder/'adapter_model.safetensors';temporary=path.with_suffix('.safetensors.part')
            try:
                save_file(state,str(temporary),metadata={'format':'pt'});temporary.replace(path)
            finally:
                if temporary.exists():temporary.unlink()
            adapter_sha=sha(path)
            atomic_json(folder/'manifest.json',{'base_revision':model.base.info['revision'],'adapter_sha256':adapter_sha,
                'runtime_enabled':False,'scope':CONFIG['scope'],'fold':fold,'training_ids':[r['id'] for r in training],
                'validation_ids':[r['id'] for r in validation],'prompt_sha256':report['identity']['prompt_sha256']})
            restored=set_peft_model_state_dict(net,load_file(str(path),device='cuda'))
            if restored.unexpected_keys or any('lora_' in key for key in restored.missing_keys):raise ValueError('候选权重重载不完整')
            again=model.predict_prepared(inputs.prepared(validation[0]['id']))
            np.testing.assert_allclose(list(again['scores'].values()),list(predictions[0]['scores'].values()),rtol=0,atol=1e-6)
            result={'fold':fold,'training_subjects':sorted({r['source_subject'] for r in training}),
                'validation_subjects':sorted({r['source_subject'] for r in validation}),
                'requested_fit_ids':[r['id'] for r in training],'gradient_ids':sorted(exposure),
                'gradient_exposure_counts':dict(exposure),'validation_ids':[r['id'] for r in validation],
                'initial_adapter_sha256':initial_sha,'adapter_path':path.relative_to(ROOT).as_posix(),'adapter_sha256':adapter_sha,
                'optimizer_updates':updates,'micro_steps':len(losses),'loss_first_32':float(np.mean(losses[:32])),
                'loss_last_32':float(np.mean(losses[-32:])),'metrics':condition_metrics(predictions),
                'predictions':predictions,'history':history,'serialization_check':'same_output_after_reload'}
            report['folds'].append(result);atomic_json(REPORT,report);print(json.dumps({'completed_fold':fold,'metrics':result['metrics']}),flush=True)
            del optimizer
        if len(all_predictions)!=len(rows) or {r['id'] for r in all_predictions}!={r['id'] for r in rows}:
            raise ValueError('折外预测分母不完整')
        for row in rows:
            if sha(project_path(row['path']))!=row['source_sha256']:raise ValueError('训练期间原视频变化')
        if report['identity']!=identity(model,rows):raise ValueError('训练期间实现或协议变化')
        metrics=condition_metrics(all_predictions)
        report.update(state='completed',metrics=metrics,predictions=all_predictions,
            gate=release_gate(metrics,report['folds']),elapsed_seconds=round(time.perf_counter()-began,1),
            limitations=['GB/PB是整次作者标签，不能当作每帧圆背真值或精确起点。',
                '这97段已有历史开发使用；本次人物折隔离不等于全新盲测或跨数据集验收。',
                '旧40段test未被本次读取；开发结果不能替代发布门禁或真实相机验证。'])
        report.pop('current_progress',None);atomic_json(REPORT,report)
        print(json.dumps({'state':report['state'],'optimizer_updates':report['optimizer_updates'],'metrics':metrics,'gate':report['gate']}),flush=True)
    except BaseException as error:
        report.update(state='interrupted' if isinstance(error,KeyboardInterrupt) else 'failed',
            error={'type':type(error).__name__,'message':str(error)[:1500]})
        atomic_json(REPORT,report);raise
    finally:
        inputs.prepared.cache_clear();model.base.model=None
        if torch.cuda.is_available():torch.cuda.empty_cache()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--prepare-only',action='store_true')
    if parser.parse_args().prepare_only:prepare()
    else:fit()
