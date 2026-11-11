"""核查已有本地视觉权重能否观察侧倾；不上传视频，不把模型投票当训练真值。"""
import argparse
import gc
import hashlib
import json
from pathlib import Path
import re
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment,project_path
configure_environment()
import av
import numpy as np
import torch
from fitness_app.video_vlm import VideoActionVLM
from fitness_app.research_extract import atomic_json

PROMPT=(
    '观察这段连续画面中的主要训练者。仅检查躯干是否持续向身体左侧或右侧倾斜，'
    '参照背景器械的竖直方向以及肩膀和髋部的相对位置。不要把相机倾斜、'
    '正常前后倾、抬手或肩膀轻微不对称直接当作侧倾。不判断肌肉发力或握力。'
    '只输出一个代码：0=未观察到明显持续侧倾；1=观察到明显持续侧倾；'
    '2=看不清、目标不明确或证据不足。不要输出解释。\n代码：'
)
DESCRIPTION_PROMPT=(
    '观察视频中坐在中央器械上的训练者，仅复核可见的躯干侧倾。'
    '先用一句话客观描述肩线、肩髋中点与附近器械竖线的关系，再给出可见结论。'
    '区分身体向左右一侧偏斜与正常前后倾、抬手、相机倾斜。证据不足时使用uncertain，'
    '不能因为没有把握就声称姿态正确。不猜测肌肉发力、握力或精确角度。'
    '只输出JSON：{"observation":"一句仅依据可见部位的描述",'
    '"visible_side_lean":"observed或not_observed或uncertain"}。'
)
OUTPUT=ROOT/'data/verification/round3/local-visual-correction-review.json'
WINDOWS=((1000,3000),(4500,6500),(8000,10000))


def samples(item,start,end):
    path=project_path(item['video']['path'])
    if hashlib.sha256(path.read_bytes()).hexdigest()!=item['video']['sha256']:
        raise ValueError('本地视觉输入来源已变化')
    conversion=item['conversion'];left,top=conversion['foreground_offset'];width,height=conversion['foreground_size']
    wanted=np.linspace(start,end,6);images=[];times=[];index=0
    with av.open(str(path)) as container:
        container.seek(start*1000)
        for frame in container.decode(video=0):
            if frame.time is None:continue
            stamp=round(float(frame.time)*1000)
            if stamp<wanted[index]:continue
            rgb=frame.to_ndarray(format='rgb24')
            if not (0<=left<left+width<=rgb.shape[1] and 0<=top<top+height<=rgb.shape[0]):
                raise ValueError('登记的原始画面区域超出视频')
            images.append(np.ascontiguousarray(rgb[top:top+height,left:left+width]));times.append(stamp);index+=1
            if index==len(wanted):break
    if len(images)!=6 or any(b<=a for a,b in zip(times,times[1:])):
        raise ValueError('本地视觉缺少时间递增的真实画面')
    return images,times,[left,top,width,height]


def run(variant='2b',mode='choice'):
    manifest=json.loads((ROOT/'data/presentation-assets.json').read_text(encoding='utf-8'))
    selected_prompt=PROMPT if mode=='choice' else DESCRIPTION_PROMPT
    model=VideoActionVLM(variant);document={'schema_version':1,'state':'running',
        'scope':'local_pretrained_visual_side_lean_feasibility_on_same_user_development_pair_not_accuracy',
        'model_repo':model.info['repo'],'model_revision':model.info['revision'],
        'model_sha256':model.info['files'].get('model.safetensors',{}).get('sha256'),'new_training_performed':False,
        'weight_files_sha256':{name:item['sha256'] for name,item in model.info['files'].items() if name.endswith('.safetensors')},
        'quantized_nf4':model.quantized,
        'prompt':selected_prompt,'prompt_sha256':hashlib.sha256(selected_prompt.encode()).hexdigest(),
        'inference_mode':mode,'max_new_tokens':160 if mode=='description' else 1,
        'private_media_uploaded':False,'network_model_loading':False,'production_enabled':False,
        'specific_window_labels_available':False,'rows':[]}
    previous=json.loads(OUTPUT.read_text(encoding='utf-8')) if OUTPUT.exists() else None
    key=variant+('_nf4' if model.quantized else '')+('_description' if mode=='description' else '')
    baseline=variant=='2b' and mode=='choice'
    existing=previous if baseline else (previous or {}).get('additional_variants',{}).get(key)
    if existing is not None:
        if existing.get('state')!='completed':raise ValueError('已有未完成检查，先检查失败原因，不覆盖结果')
        if existing.get('prompt_sha256')!=document['prompt_sha256'] or existing.get('model_revision')!=model.info['revision']:
            raise ValueError('既有检查身份已变化，不覆盖结果')
        print(json.dumps({'cached_variant':key,'rows':len(existing['rows'])}),flush=True);return
    if not baseline and (not previous or previous.get('state')!='completed'):
        raise ValueError('先完成2B基线，不改用另一模型掩盖原失败')
    def write():
        if baseline:atomic_json(OUTPUT,document)
        else:
            previous.setdefault('additional_variants',{})[key]=document
            atomic_json(OUTPUT,previous)
    write()
    try:
        torch.set_num_threads(4);model.load()
        codes=['0','1','2'];token_ids=[model.processor.tokenizer.encode(c,add_special_tokens=False) for c in codes]
        if any(len(v)!=1 for v in token_ids):raise ValueError('侧倾候选代码不是单个token')
        ids=torch.tensor([v[0] for v in token_ids],device='cuda')
        for number in ('1688','1689'):
            item=manifest['demos']['user-'+number]
            for start,end in WINDOWS:
                images,times,crop=samples(item,start,end)
                content=[{'type':'video'},{'type':'text','text':selected_prompt}]
                text=model.processor.apply_chat_template([{'role':'user','content':content}],tokenize=False,add_generation_prompt=True)
                count=len(images);span=times[-1]-times[0]
                metadata={'fps':(count-1)*1000/span,'total_num_frames':count,
                    'duration':span/1000,'frames_indices':list(range(count))}
                began=time.perf_counter()
                inputs=model.processor(text=[text],videos=[np.asarray(images)],return_tensors='pt',
                    videos_kwargs={'do_sample_frames':False,'video_metadata':[metadata],
                        'size':{'shortest_edge':count*64*64,'longest_edge':count*384*384},
                        'cap_pixels_per_frame':True}).to('cuda')
                with torch.inference_mode():
                    if mode=='choice':
                        output=model.model(**inputs,logits_to_keep=1,use_cache=False)
                        probabilities=output.logits[0,-1].float().softmax(-1)
                        scores=probabilities[ids].cpu().numpy()
                        top=int(scores.argmax())
                        prediction={'top_code':codes[top],'scores':dict(zip(codes,map(float,scores))),
                            'code_mass':float(scores.sum()),'score_is_calibrated_probability':False}
                    else:
                        output=model.model.generate(**inputs,max_new_tokens=160,do_sample=False,use_cache=True)
                        answer=model.processor.batch_decode(output[:,inputs['input_ids'].shape[1]:],skip_special_tokens=True)[0]
                        prediction={'raw_text':answer,'parsed':None,'parse_status':'invalid_json_or_schema'}
                        match=re.fullmatch(r'\s*(?:```json\s*)?(\{.*\})(?:\s*```)?\s*',answer,re.S)
                        try:
                            parsed=json.loads(match.group(1)) if match else None
                            if (isinstance(parsed,dict) and set(parsed)=={'observation','visible_side_lean'} and
                                isinstance(parsed['observation'],str) and 0<len(parsed['observation'])<=800 and
                                parsed['visible_side_lean'] in {'observed','not_observed','uncertain'}):
                                prediction['parsed']=parsed
                                prediction['parse_status']='valid'
                        except (ValueError,TypeError) as error:
                            prediction['parse_error_type']=type(error).__name__
                torch.cuda.synchronize()
                row={'demo_id':item['id'],'source_sha256':item['video']['sha256'],
                    'source_ms':times,'foreground_crop_pixels':crop,
                    'user_video_description':item['user_reported_quality'],
                    'window_truth':None,**prediction,
                    'elapsed_ms':round((time.perf_counter()-began)*1000,1),
                    'video_grid_thw':inputs['video_grid_thw'].cpu().tolist()}
                document['rows'].append(row);write()
                print(json.dumps(row,ensure_ascii=False),flush=True)
                del inputs,output
                if mode=='choice':del probabilities
        document['state']='completed';document['peak_cuda_allocated_bytes']=torch.cuda.max_memory_allocated()
        decisions={r.get('top_code') if mode=='choice' else (r.get('parsed') or {}).get('visible_side_lean') for r in document['rows']}
        document['all_top_codes_identical']=len(decisions)==1
        document['release_gate_passed']=False
        document['remaining_gates']=['独立具体错误标签及误报漏报验证','分数校准','实时资源与队列接线']
        write()
    except Exception as error:
        document.update(state='failed',error={'type':type(error).__name__,'message':str(error)[:1000]})
        write();raise
    finally:
        model.model=None;model.processor=None;gc.collect()
        if torch.cuda.is_available():torch.cuda.empty_cache()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--variant',choices=('2b','4b'),default='2b')
    parser.add_argument('--mode',choices=('choice','description'),default='choice')
    args=parser.parse_args();run(args.variant,args.mode)
