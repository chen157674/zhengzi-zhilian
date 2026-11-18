"""本地小型视觉语言模型的动作候选；尚未通过验证时不由实时路由启用。"""
import hashlib,json,string,time
from pathlib import Path
import numpy as np
from .paths import ROOT,project_path
from .catalogue import DEFINITIONS

DIRECTORY=ROOT/'data/models/qwen3-vl-2b-instruct'
LABELS=[None]+[r[0] for r in DEFINITIONS]
CODES=['0']+list(string.ascii_uppercase)+list('1234567')
CONFIG={'schema':'qwen-video-action-choice-4','frames':8,'minimum_span_ms':1200,'maximum_span_ms':4000,
        'maximum_pixels':384*384,'minimum_pixels':64*64,'minimum_score':.55,'minimum_margin':.15,
        'minimum_code_mass':.6,'pixel_budget_scope':'per_frame_area; processor total budget = frames * area','runtime_enabled':False}

HINTS={
 'barbell_overhead_press':'站姿，一根长杠铃从上胸推到头顶',
 'barbell_bent_over_row':'保持俯身，用杠铃向腹部拉，再放下',
 'conventional_deadlift':'杠铃从地面起拉，髋膝一起伸直到站直',
 'barbell_bench_press':'躺平板凳，屈伸肘向上推一根杠铃',
 'dumbbell_bench_press':'躺平板凳，屈伸肘向上推两只哑铃',
 'incline_dumbbell_press':'背靠倾斜凳，向上推两只哑铃',
 'machine_chest_press':'坐姿背靠垫，屈伸肘把机器把手向前推出',
 'pec_deck_fly':'坐姿背靠垫，双臂从两侧向胸前夹合',
 'dumbbell_chest_fly':'仰卧，哑铃向两侧展开再夹合，肘角变化较小',
 'cable_chest_fly':'站姿，双绳索从身体两侧向胸前夹合',
 'cable_triceps_pushdown':'站姿肘在体侧，将高位绳索把手向下压',
 'overhead_dumbbell_extension':'上臂在头旁，屈伸肘让哑铃在脑后升降',
 'reverse_pec_deck_fly':'胸口朝机器垫，双臂向身后两侧张开',
 'dumbbell_shoulder_press':'两只哑铃从肩旁推到头顶，上臂也抬起',
 'cable_y_raise':'绳索，近直臂向斜上方举起呈Y形',
 'dumbbell_lateral_raise':'直立或坐姿，哑铃从体侧向左右抬起',
 'bent_over_dumbbell_reverse_fly':'躯干前倾，哑铃向左右两侧展开',
 'dumbbell_front_raise':'直立或坐姿，哑铃从身前向正前方抬起',
 'assisted_pull_up':'膝盖或脚在器械辅助平台上，身体向固定横杆拉起',
 'pull_up':'身体悬挂在固定横杆下，身体向上拉起',
 'lat_pulldown':'坐姿，手把头顶上方的机器把手或长杆向下拉',
 'seated_cable_row':'坐姿，手把身前的低位绳索水平拉向腰腹',
 'cable_face_pull':'绳索拉向面部，双肘向左右展开',
 'standing_dumbbell_curl':'站姿哑铃弯举，手掌朝前或转向上',
 'hammer_curl':'哑铃弯举，手掌始终相对，拇指向上',
}


def prompt():
    choices=['0: 无法确定、未运动、准备/休息或列表外动作']
    choices.extend(f'{code}: {row[1]} ({row[0].replace("_"," ")})'+('；'+HINTS[row[0]] if row[0] in HINTS else '') for code,row in zip(CODES[1:],DEFINITIONS))
    return ('观察这段连续视频，判断训练者实际正在做的主要健身动作。结合器械、身体朝向以及手臂/腿的运动，不根据字幕猜动作。'
            '空手讲解、调整器械、未进行训练、证据不足或列表之外均选0。仅坐在机器上但手不握把讲解，不能按机器猜动作。平板支撑可以静态保持。'
            '幅度不足、借力等不标准表现仍可能属于同一种练习，不要仅因不标准就改成别的动作。'
            '注意杠铃/哑铃、卧推/飞鸟、夹胸/反向飞鸟、下拉/引体不同。'
            '不要判断肌肉发力或姿态好坏。只输出下面唯一对应的一个代码字符，不加标点和解释。\n'+'\n'.join(choices)+'\n动作代码：')


class VideoActionVLM:
    def __init__(self,variant='2b',quantized=False,adapter=None):
        if variant not in {'2b','4b'}:raise ValueError('未知视觉候选型号')
        self.variant=variant;self.quantized=bool(quantized or variant=='4b');self.adapter=project_path(adapter) if adapter else None
        self.directory=ROOT/f'data/models/qwen3-vl-{variant}-instruct'
        self.processor=self.model=None;self.code_ids=None
        self.info=json.loads((self.directory/'manifest.json').read_text(encoding='utf-8'))
        if self.info['status']!='verified' or self.info['repo']!=f'Qwen/Qwen3-VL-{variant.upper()}-Instruct':raise ValueError('请准备已验证的视觉模型')
        self.version=f'qwen3-vl-{variant}-'+self.info['revision'][:12]+'-'+hashlib.sha256((json.dumps(CONFIG,sort_keys=True)+prompt()).encode()).hexdigest()[:8]+('-nf4' if self.quantized else '')

    def load(self):
        if self.model is not None:return
        import torch
        from transformers import AutoProcessor,Qwen3VLForConditionalGeneration,BitsAndBytesConfig
        if len(LABELS)!=len(CODES):raise ValueError('视觉模型动作选项数变化，需要更新代码契约')
        for name,item in self.info['files'].items():
            if hashlib.sha256(project_path(self.directory/name).read_bytes()).hexdigest()!=item['sha256']:raise ValueError('视觉模型文件摘要不符')
        if not torch.cuda.is_available():raise RuntimeError('本地视觉候选需要已验证的CUDA环境')
        self.processor=AutoProcessor.from_pretrained(self.directory,local_files_only=True)
        encoded=[self.processor.tokenizer.encode(c,add_special_tokens=False) for c in CODES]
        if any(len(ids)!=1 for ids in encoded):raise ValueError('候选代码必须对应单个token')
        self.code_ids=torch.tensor([ids[0] for ids in encoded],device='cuda')
        extra={}
        if self.quantized:extra['quantization_config']=BitsAndBytesConfig(load_in_4bit=True,bnb_4bit_quant_type='nf4',bnb_4bit_use_double_quant=True,bnb_4bit_compute_dtype=torch.bfloat16,llm_int8_skip_modules=['visual','lm_head'])
        self.model=Qwen3VLForConditionalGeneration.from_pretrained(self.directory,local_files_only=True,dtype=torch.bfloat16,attn_implementation='sdpa',device_map={'':'cuda'},**extra).eval()
        if self.adapter:
            from peft import PeftModel
            manifest=json.loads((self.adapter/'manifest.json').read_text(encoding='utf-8'))
            if manifest['base_revision']!=self.info['revision'] or hashlib.sha256((self.adapter/'adapter_model.safetensors').read_bytes()).hexdigest()!=manifest['adapter_sha256']:raise ValueError('适配器归属或摘要不符')
            self.model=PeftModel.from_pretrained(self.model,self.adapter,is_trainable=False,local_files_only=True).eval()
            self.version+='-'+manifest['adapter_sha256'][:12]

    def predict(self,frames,timestamps,observed=None,detail_rgb=None,return_embedding=False):
        import torch
        if len(frames)!=CONFIG['frames'] or len(timestamps)!=CONFIG['frames'] or any(b<=a for a,b in zip(timestamps,timestamps[1:])):raise ValueError('需要配置数量的时间递增真实画面')
        span=timestamps[-1]-timestamps[0]
        if not CONFIG['minimum_span_ms']<=span<=CONFIG['maximum_span_ms']:raise ValueError('视频片段长度不符')
        if any(f.ndim!=3 or f.shape[2]!=3 or f.dtype!=np.uint8 for f in frames):raise ValueError('视频画面必须为RGB uint8')
        self.load();began=time.perf_counter()
        inputs=self.prepare_inputs(frames,timestamps,observed,detail_rgb)
        captured=[];hook=None
        if return_embedding:
            base=self.model.get_base_model() if hasattr(self.model,'get_base_model') else self.model
            hook=base.model.language_model.norm.register_forward_hook(lambda module,args,value:captured.append(value[:,-1].detach()))
        try:
            with torch.inference_mode():
                output=self.model(**inputs,logits_to_keep=1,use_cache=False)
                full=output.logits[0,-1].float().softmax(-1);prob=full[self.code_ids];order=prob.argsort(descending=True)
        finally:
            if hook:hook.remove()
        torch.cuda.synchronize();scores=prob.cpu().numpy();top=int(order[0]);score=float(scores[top]);margin=score-float(scores[int(order[1])]);mass=float(scores.sum())
        accepted=score>=CONFIG['minimum_score'] and margin>=CONFIG['minimum_margin'] and mass>=CONFIG['minimum_code_mass']
        result={'action_id':LABELS[top] if accepted else None,'top_action_id':LABELS[top],'score':score,'margin':margin,'code_mass':mass,
                'reason':None if accepted and top else 'video_background' if accepted else 'video_uncertain',
                'scores':dict(zip([a or 'unknown' for a in LABELS],map(float,scores))),
                'score_is_calibrated_probability':False,'decision_basis':'local_video_language_candidate',
                'model_version':self.version,'elapsed_ms':round((time.perf_counter()-began)*1000,1),
                'processed_video_grid_thw':inputs['video_grid_thw'].cpu().tolist(),'pose_observations':observed or {},'upper_body_detail_used':detail_rgb is not None,
                'specific_error_validation_passed':False,'runtime_enabled':False}
        if return_embedding:
            if len(captured)!=1:raise ValueError('视频语义特征输出不唯一')
            result['_embedding']=captured[0][0].float().cpu().numpy()
        return result

    def prepare_inputs(self,frames,timestamps,observed=None,detail_rgb=None):
        if self.processor is None:raise RuntimeError('请准备视觉处理资源')
        span=timestamps[-1]-timestamps[0]
        from .recognition_observations import describe
        content=[{'type':'video'}]
        if detail_rgb is not None:content.append({'type':'image'})
        text_prompt=prompt()
        if observed:text_prompt+='\n'+describe(observed)+'\n观测有误差，请结合视频的器械及实际运动判断，不能仅靠一个角度数值。'
        if detail_rgb is not None:text_prompt+='\n附图是同一训练者上半身的放大细节，不是另一位人物；整体动作以视频为准。'
        content.append({'type':'text','text':text_prompt})
        messages=[{'role':'user','content':content}]
        text=self.processor.apply_chat_template(messages,tokenize=False,add_generation_prompt=True)
        count=len(frames);metadata={'fps':(count-1)*1000/span,'total_num_frames':count,'duration':span/1000,'frames_indices':list(range(count))}
        extra={}
        if detail_rgb is not None:extra={'images':[detail_rgb],'images_kwargs':{'size':{'shortest_edge':CONFIG['minimum_pixels'],'longest_edge':CONFIG['maximum_pixels']}}}
        inputs=self.processor(text=[text],videos=[np.asarray(frames)],return_tensors='pt',**extra,
                              videos_kwargs={'do_sample_frames':False,'video_metadata':[metadata],
                                             'size':{'shortest_edge':count*CONFIG['minimum_pixels'],'longest_edge':count*CONFIG['maximum_pixels']},
                                             'cap_pixels_per_frame':True})
        return inputs.to('cuda')
