"""完整硬拉GB/PB来源条件候选；未通过门禁时不注册生产纠错。"""
import hashlib
import json
import time
import numpy as np
from .video_vlm import VideoActionVLM
from .paths import project_path

PROMPT=(
    '这是一位训练者完成一次传统杠铃硬拉的连续画面。只区分该次动作的背部形态：'
    'A=未观察到明显圆背；B=观察到明显圆背。根据背部可见轮廓及整次运动判断，'
    '正常的髋部折叠和躯干整体前倾本身不属于圆背；不要根据衣服、人物或背景选类别。'
    '不推断肌肉发力、负重、安全性或精确脊柱角度。只输出A或B，不加解释。\n代码：'
)
CONFIG={'schema':'whole-rep-back-condition-1','frames':8,'maximum_pixels':384*384,
    'minimum_pixels':64*64,'minimum_span_ms':400,'maximum_span_ms':15000,
    'minimum_score':.8,'labels':{'A':'GB','B':'PB'},'scope':'publisher_whole_repetition_GB_PB_condition_only',
    'production_enabled':False,'score_is_calibrated_probability':False}


def validate_video(frames,times):
    if len(frames)!=CONFIG['frames'] or len(times)!=CONFIG['frames']:
        raise ValueError('需要八张真实观测画面')
    if any(type(t) is not int or t<0 for t in times) or any(b<=a for a,b in zip(times,times[1:])):
        raise ValueError('源时间必须非负且递增')
    if not CONFIG['minimum_span_ms']<=times[-1]-times[0]<=CONFIG['maximum_span_ms']:
        raise ValueError('完整周期时长不在候选范围')
    if any(not isinstance(image,np.ndarray) or image.dtype!=np.uint8 or image.ndim!=3 or
           image.shape[2]!=3 or min(image.shape[:2])<32 or max(image.shape[:2])>4096 for image in frames):
        raise ValueError('画面必须是有界RGB uint8')
    if len({image.shape for image in frames})!=1:raise ValueError('周期内画面尺寸变化')


class VisualBackConditionCandidate:
    def __init__(self,adapter=None):
        self.base=VideoActionVLM('2b',quantized=True,adapter=adapter)
        self.choice_ids=None
        self.version='visual-back-'+hashlib.sha256(json.dumps({'base':self.base.info['revision'],
            'config':CONFIG,'prompt':PROMPT},sort_keys=True).encode()).hexdigest()[:12]

    def load_processor(self):
        if self.base.processor is not None:return self.base.processor
        from transformers import AutoProcessor
        for name,item in self.base.info['files'].items():
            if name.endswith('.safetensors'):continue
            path=project_path(self.base.directory/name)
            with path.open('rb') as stream:actual=hashlib.file_digest(stream,'sha256').hexdigest()
            if actual!=item['sha256']:raise ValueError('视觉纠错处理器来源不符')
        self.base.processor=AutoProcessor.from_pretrained(self.base.directory,local_files_only=True,trust_remote_code=False)
        return self.base.processor

    def load(self):
        import torch
        self.base.load()
        if self.choice_ids is None:
            encoded=[self.base.processor.tokenizer.encode(code,add_special_tokens=False) for code in CONFIG['labels']]
            if any(len(ids)!=1 for ids in encoded):raise ValueError('纠错条件代码不是单token')
            self.choice_ids=torch.tensor([ids[0] for ids in encoded],device='cuda')

    def prepare(self,frames,times):
        validate_video(frames,times);processor=self.load_processor()
        messages=[{'role':'user','content':[{'type':'video'},{'type':'text','text':PROMPT}]}]
        text=processor.apply_chat_template(messages,tokenize=False,add_generation_prompt=True)
        count=len(frames);span=times[-1]-times[0]
        metadata={'fps':(count-1)*1000/span,'total_num_frames':count,'duration':span/1000,'frames_indices':list(range(count))}
        return processor(text=[text],videos=[np.asarray(frames)],return_tensors='pt',
            videos_kwargs={'do_sample_frames':False,'video_metadata':[metadata],
                'size':{'shortest_edge':count*CONFIG['minimum_pixels'],'longest_edge':count*CONFIG['maximum_pixels']},
                'cap_pixels_per_frame':True})

    def predict_prepared(self,prepared):
        import torch
        self.load();began=time.perf_counter()
        inputs={key:value.to('cuda') for key,value in prepared.items()}
        self.base.model.eval()
        with torch.inference_mode():
            logits=self.base.model(**inputs,logits_to_keep=1,use_cache=False).logits[0,-1].float()
            scores=logits.softmax(-1)[self.choice_ids].cpu().numpy()
        if not np.isfinite(scores).all():raise ValueError('视觉纠错输出非有限值')
        top=int(scores.argmax());labels=list(CONFIG['labels'].values());score=float(scores[top])
        return {'top':labels[top],'accepted':labels[top] if score>=CONFIG['minimum_score'] else None,
            'score':score,'scores':dict(zip(labels,map(float,scores))),'code_mass':float(scores.sum()),
            'score_is_calibrated_probability':False,'production_enabled':False,
            'elapsed_ms':round((time.perf_counter()-began)*1000,2)}

    def observe(self,frames,times,*,action_id,phase_complete,single_person=True,torso_visible=True):
        reason=None
        if action_id!='conventional_deadlift':reason='unsupported_exercise'
        elif phase_complete is not True:reason='awaiting_complete_cycle'
        elif single_person is not True:reason='target_not_unique'
        elif torso_visible is not True:reason='torso_not_visible'
        if reason:return {'status':'unassessable','reason':reason,'production_enabled':False}
        try:validate_video(frames,times)
        except ValueError as error:
            return {'status':'unassessable','reason':'invalid_video_window','detail':str(error),'production_enabled':False}
        result=self.predict_prepared(self.prepare(frames,times))
        return {**result,'status':'candidate' if result['accepted'] else 'unassessable',
            'reason':None if result['accepted'] else 'visual_condition_uncertain',
            'time_range_ms':[times[0],times[-1]],'temporal_location':'whole_completed_rep_not_frame_label',
            'scope':CONFIG['scope'],'model_version':self.version}
