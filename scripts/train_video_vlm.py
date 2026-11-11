"""8GB显存上的视频动作QLoRA：只用训练分区梯度，按来源隔离的开发集选候选。"""
import argparse,collections,hashlib,json,random,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment
configure_environment()
import numpy as np,torch
from transformers import AutoProcessor,Qwen3VLForConditionalGeneration,BitsAndBytesConfig
from peft import LoraConfig,get_peft_model,prepare_model_for_kbit_training,set_peft_model_state_dict
from fitness_app.video_vlm import VideoActionVLM,DIRECTORY,LABELS,CODES,CONFIG,prompt
from fitness_app.research_extract import atomic_json
from evaluate_xclip import sources,OUT
from evaluate_video_vlm import frames

DESTINATION=ROOT/'data/models/video-vlm-adapter'


def validation_entries(entries,limit=2):
    counts=collections.Counter();result=[]
    for entry in entries:
        if entry['partition']!='validation' or counts[entry['action_id']]>=limit:continue
        counts[entry['action_id']]+=1;result.append(entry)
    return result


def run(steps=300,start_step=0,resume=False):
    seed=20261023;random.seed(seed);np.random.seed(seed);torch.manual_seed(seed);torch.cuda.manual_seed_all(seed);torch.set_num_threads(4)
    entries=sources(include_legacy=True)
    groups={p:{r['group'] for r in entries if r['partition']==p} for p in ('train','validation')}
    # 新DVIDS片段仅作诊断，不能让旧训练发布者集合混进验证。
    if groups['train']&groups['validation']:raise ValueError('视频QLoRA来源组交叉')
    training=collections.defaultdict(list)
    for entry in entries:
        if entry['partition']=='train':training[entry['action_id']].append(entry)
    valid=validation_entries(entries)
    model=VideoActionVLM();model.processor=AutoProcessor.from_pretrained(DIRECTORY,local_files_only=True)
    encoded=[model.processor.tokenizer.encode(c,add_special_tokens=False) for c in CODES]
    if any(len(row)!=1 for row in encoded):raise ValueError('动作代码不是单个token')
    model.code_ids=torch.tensor([r[0] for r in encoded],device='cuda')
    quant=BitsAndBytesConfig(load_in_4bit=True,bnb_4bit_quant_type='nf4',bnb_4bit_use_double_quant=True,bnb_4bit_compute_dtype=torch.bfloat16,
                             llm_int8_skip_modules=['visual','lm_head'])
    net=Qwen3VLForConditionalGeneration.from_pretrained(DIRECTORY,local_files_only=True,dtype=torch.bfloat16,attn_implementation='sdpa',
                                                       quantization_config=quant,device_map={'':'cuda'})
    net=prepare_model_for_kbit_training(net,use_gradient_checkpointing=True,gradient_checkpointing_kwargs={'use_reentrant':False})
    layers=net.config.text_config.num_hidden_layers
    configuration=LoraConfig(r=8,lora_alpha=16,lora_dropout=.05,target_modules=['q_proj','k_proj','v_proj','o_proj'],
                             layers_to_transform=list(range(max(0,layers-8),layers)),layers_pattern='layers',bias='none',task_type='CAUSAL_LM')
    snapshot={k:(sorted(v) if isinstance(v,set) else v) for k,v in configuration.to_dict().items()}
    json.dumps(snapshot)  # 训练前验证第三方配置可序列化，避免完成后才丢失记录。
    net=get_peft_model(net,configuration);net.config.use_cache=False;model.model=net
    resume_digest=None
    if resume:
        from safetensors.torch import load_file
        checkpoint=DESTINATION/'adapter_model.safetensors'
        resume_digest=hashlib.sha256(checkpoint.read_bytes()).hexdigest()
        values=load_file(str(checkpoint),device='cuda')
        if any(not torch.isfinite(v).all() for v in values.values()):raise ValueError('续训权重非有限值')
        status=set_peft_model_state_dict(net,values)
        if status.unexpected_keys or any('lora_' in k for k in status.missing_keys):raise ValueError('续训LoRA权重不完整')
    trainable=sum(p.numel() for p in net.parameters() if p.requires_grad)
    print(json.dumps({'trainable_parameters':trainable,'classes':len(training),'train_clips':sum(map(len,training.values())),'validation_clips':len(valid),'steps':steps}),flush=True)
    optimizer=torch.optim.AdamW([p for p in net.parameters() if p.requires_grad],lr=1e-4,weight_decay=.01)
    actions=sorted(training);label_index={a:i for i,a in enumerate(LABELS)};label_index['unknown']=0
    history=[];best=-1.;began=time.perf_counter();accumulation=4;loss_total=0.
    def evaluate():
        net.eval();predictions=[]
        for entry in valid:
            images,times=frames(entry);p=model.predict(images,times)
            predictions.append({'id':entry['id'],'expected':entry['action_id'],'group':entry['group'],'prediction':p})
        positive=[r for r in predictions if r['expected']!='unknown'];negative=[r for r in predictions if r['expected']=='unknown']
        recalls=[]
        for action in sorted({r['expected'] for r in positive}):
            rows=[r for r in positive if r['expected']==action];recalls.append(sum(r['prediction']['action_id']==action for r in rows)/len(rows))
        metrics={'clips':len(positive),'accuracy_including_rejections':sum(r['prediction']['action_id']==r['expected'] for r in positive)/max(1,len(positive)),
                 'top1':sum(r['prediction']['top_action_id']==r['expected'] for r in positive)/max(1,len(positive)),
                 'macro_recall':float(np.mean(recalls)),'negative_false_accepts':sum(r['prediction']['action_id'] is not None for r in negative),'negative_clips':len(negative)}
        net.train();return metrics,predictions
    # 原始量化模型的真实对照，不能用训练后结果倒推基线。
    baseline,predictions=evaluate();print(json.dumps({'baseline':baseline,'resumed':resume,'start_step':start_step}),flush=True)
    atomic_json(OUT/'video-vlm-training.json',{'schema_version':1,'status':'training','baseline':baseline,'baseline_origin':'resumed checkpoint' if resume else 'unadapted quantized model',
                                            'start_step':start_step,'resume_checkpoint_sha256':resume_digest,'optimizer_reset_on_resume':resume,'target_steps':steps,
                                            'train_actions':sorted(training),'feature_config':CONFIG,'runtime_enabled':False,'history':[]})
    optimizer.zero_grad(set_to_none=True)
    for step in range(start_step,steps):
        action=actions[step%len(actions)]
        if step%len(actions)==0:random.shuffle(actions)
        entry=random.choice(training[action]);length=entry['end_ms']-entry['start_ms']
        if length>4000:
            center=random.uniform(entry['start_ms']+1600,entry['end_ms']-1600);entry={**entry,'start_ms':round(center-1600),'end_ms':round(center+1600)}
        images,times=frames(entry);inputs=model.prepare_inputs(images,times);target=model.code_ids[label_index[action]][None]
        net.train()
        with torch.autocast('cuda',dtype=torch.bfloat16):
            logits=net(**inputs,logits_to_keep=1,use_cache=False).logits[:,-1].float()
            loss=torch.nn.functional.cross_entropy(logits,target)
        if not torch.isfinite(loss):raise ValueError('QLoRA训练损失非有限值')
        (loss/accumulation).backward();loss_total+=float(loss.detach())
        if (step+1)%accumulation==0:
            torch.nn.utils.clip_grad_norm_([p for p in net.parameters() if p.requires_grad],1.);optimizer.step();optimizer.zero_grad(set_to_none=True)
        if (step+1)%25==0:print(json.dumps({'step':step+1,'loss_mean':loss_total/(step+1),'elapsed_seconds':round(time.perf_counter()-began,1),'allocated_mib':round(torch.cuda.max_memory_allocated()/2**20,1)}),flush=True)
        if (step+1)%100==0 or step+1==steps:
            metrics,predictions=evaluate();score=metrics['macro_recall']-.1*metrics['negative_false_accepts']
            history.append({'step':step+1,'metrics':metrics});print(json.dumps(history[-1]),flush=True)
            if score>best:
                best=score;DESTINATION.resolve().relative_to(ROOT);DESTINATION.mkdir(exist_ok=True)
                net.save_pretrained(DESTINATION,safe_serialization=True,save_embedding_layers=False)
                identity={'base_revision':model.info['revision'],'training_inputs':[{k:r[k] for k in ('id','action_id','group','source_sha256','start_ms','end_ms','crop')} for r in entries if r['partition']=='train'],
                          'prompt_sha256':hashlib.sha256(prompt().encode()).hexdigest(),'feature_config':CONFIG,'lora':snapshot,'seed':seed,
                          'start_step':start_step,'resume_checkpoint_sha256':resume_digest,'optimizer_reset_on_resume':resume}
                report={'schema_version':1,'identity':identity,'selection':'development macro recall minus false-positive penalty; no blind test','steps_completed':step+1,
                        'baseline':baseline,'baseline_origin':'resumed checkpoint' if resume else 'unadapted quantized model',
                        'validation':metrics,'predictions':predictions,'history':history,'runtime_enabled':False,
                        'trainable_parameters':trainable,'train_actions':sorted(training),'test_used':False,'specific_error_validation_passed':False,
                        'hardware':{'gpu':torch.cuda.get_device_name(0),'torch':torch.__version__,'peak_allocated_mib':round(torch.cuda.max_memory_allocated()/2**20,1)},
                        'adapter_files':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in DESTINATION.iterdir() if p.is_file() and p.name!='manifest.json'}}
                atomic_json(OUT/'video-vlm-training.json',report)
                atomic_json(DESTINATION/'manifest.json',{'base_revision':model.info['revision'],'runtime_enabled':False,'validation':metrics,'adapter_sha256':report['adapter_files']['adapter_model.safetensors'],'steps':step+1})
    print(json.dumps({'finished_steps':steps,'best_score':best,'elapsed_seconds':round(time.perf_counter()-began,1)}),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--steps',type=int,default=300);parser.add_argument('--start-step',type=int,default=0);parser.add_argument('--resume-adapter',action='store_true');args=parser.parse_args();run(args.steps,args.start_step,args.resume_adapter)
