"""短上下文视频候选对照，减少33类长提示的显存开销；未通过验证不发布。"""
import hashlib
import json
import time
import numpy as np
from .video_vlm import VideoActionVLM,CONFIG,LABELS,CODES

DESCRIPTIONS={
 'bodyweight_squat':'bodyweight squat',
 'standard_push_up':'push-up on toes, straight legs',
 'reverse_lunge':'bodyweight backward lunge',
 'bodyweight_glute_bridge':'glute bridge, supine hip lift',
 'bent_knee_sit_up':'bent-knee sit-up',
 'forearm_plank':'static forearm plank',
 'jumping_jack':'jumping jack, arms and legs apart/together',
 'high_knees':'running high knees',
 'standing_dumbbell_curl':'standing dumbbell biceps curl, supinated grip',
 'barbell_overhead_press':'standing barbell overhead shoulder press',
 'barbell_bent_over_row':'bent-over barbell row towards abdomen',
 'conventional_deadlift':'barbell deadlift from floor to upright',
 'barbell_bench_press':'flat bench barbell chest press',
 'dumbbell_bench_press':'flat bench dumbbell chest press',
 'incline_dumbbell_press':'incline bench dumbbell chest press',
 'machine_chest_press':'seated machine chest press, extending elbows forward',
 'pec_deck_fly':'pec deck chest fly, bringing arms together',
 'dumbbell_chest_fly':'supine dumbbell fly, opening/closing arms, elbows nearly fixed',
 'cable_chest_fly':'standing cable chest fly, bringing arms together',
 'cable_triceps_pushdown':'standing cable triceps pushdown, upper arms at sides',
 'overhead_dumbbell_extension':'overhead dumbbell triceps extension, weight behind head',
 'reverse_pec_deck_fly':'reverse pec deck rear delt fly, chest facing pad',
 'dumbbell_shoulder_press':'dumbbell overhead shoulder press',
 'cable_y_raise':'cable Y raise, near-straight arms diagonally overhead',
 'dumbbell_lateral_raise':'dumbbell lateral raise, arms sideways',
 'bent_over_dumbbell_reverse_fly':'bent-over dumbbell rear delt fly, arms sideways',
 'dumbbell_front_raise':'dumbbell front raise, near-straight arms forward',
 'assisted_pull_up':'machine-assisted pull-up, knees/feet on support platform',
 'pull_up':'unassisted bodyweight pull-up on fixed bar',
 'lat_pulldown':'seated lat pulldown, overhead handle pulled down',
 'seated_cable_row':'seated cable row, low cable pulled to abdomen',
 'cable_face_pull':'cable face pull, rope towards face, elbows apart',
 'hammer_curl':'dumbbell hammer curl, neutral thumbs-up grip',
}
MAXIMUM_CONTEXT_MS=8000


def compact_prompt():
    if set(DESCRIPTIONS)!={a for a in LABELS if a}:raise ValueError('简短视频动作描述不完整')
    return ('Classify the exercise actually performed across this video. Use movement, equipment and body orientation; ignore captions. '
            'Poor technique still belongs to its exercise. Choose 0 for talking, setup, rest, unsupported exercise or insufficient evidence. '
            'A static forearm plank is an exercise. Reply with ONE code only.\n0: none/unknown\n'+
            '\n'.join(code+': '+DESCRIPTIONS[action] for code,action in zip(CODES[1:],LABELS[1:]))+'\nCode:')


class CompactVideoVLM(VideoActionVLM):
    def __init__(self,variant='4b',quantized=True):
        super().__init__(variant,quantized)
        self.version+='-compact-'+hashlib.sha256(compact_prompt().encode()).hexdigest()[:12]
        self.version+='-context8s'

    def predict(self,frames,timestamps,observed=None,detail_rgb=None):
        # 此候选允许慢动作跨8秒；保留真实时间，不把8秒视频谎报为4秒。
        import torch
        if len(frames)!=CONFIG['frames'] or len(timestamps)!=CONFIG['frames'] or any(b<=a for a,b in zip(timestamps,timestamps[1:])):
            raise ValueError('需要8个时间递增的真实画面')
        span=timestamps[-1]-timestamps[0]
        if not CONFIG['minimum_span_ms']<=span<=MAXIMUM_CONTEXT_MS:raise ValueError('简短上下文视频时长不符')
        if any(f.ndim!=3 or f.shape[2]!=3 or f.dtype!=np.uint8 for f in frames):raise ValueError('视频必须是RGB uint8')
        self.load();began=time.perf_counter()
        # 不改变权重/输入；释放前一窗口的空闲显存块，避免WDDM在8GB卡上反复换页。
        torch.cuda.empty_cache();torch.cuda.reset_peak_memory_stats();inputs=self.prepare_inputs(frames,timestamps,observed,detail_rgb)
        with torch.inference_mode():
            output=self.model(**inputs,logits_to_keep=1,use_cache=False)
            full=output.logits[0,-1].float().softmax(-1);prob=full[self.code_ids];order=prob.argsort(descending=True)
        torch.cuda.synchronize();scores=prob.cpu().numpy();top=int(order[0]);score=float(scores[top])
        margin=score-float(scores[int(order[1])]);mass=float(scores.sum())
        accepted=score>=CONFIG['minimum_score'] and margin>=CONFIG['minimum_margin'] and mass>=CONFIG['minimum_code_mass']
        return {'action_id':LABELS[top] if accepted else None,'top_action_id':LABELS[top],'score':score,'margin':margin,'code_mass':mass,
                'reason':None if accepted and top else 'video_background' if accepted else 'video_uncertain',
                'scores':dict(zip([a or 'unknown' for a in LABELS],map(float,scores))),
                'score_is_calibrated_probability':False,'decision_basis':'local_compact_video_choice',
                'model_version':self.version,'elapsed_ms':round((time.perf_counter()-began)*1000,1),
                'processed_video_grid_thw':inputs['video_grid_thw'].cpu().tolist(),'pose_observations':observed or {},
                'input_tokens':int(inputs['input_ids'].shape[-1]),'cuda_peak_allocated_mib':round(torch.cuda.max_memory_allocated()/1024**2,1),
                'cuda_peak_reserved_mib':round(torch.cuda.max_memory_reserved()/1024**2,1),
                'context_span_ms':int(span),'upper_body_detail_used':detail_rgb is not None,'runtime_enabled':False}

    def prepare_inputs(self,frames,timestamps,observed=None,detail_rgb=None):
        if self.processor is None:raise RuntimeError('视觉处理器尚未准备')
        text=compact_prompt()
        if observed:text+='\nApproximate projected joint observations, possibly inaccurate; use visual equipment/movement too: '+json.dumps(observed,sort_keys=True)
        content=[{'type':'video'}]
        if detail_rgb is not None:
            content.append({'type':'image'});text+='\nThe still image is a close-up of the SAME person, not a second person.'
        content.append({'type':'text','text':text})
        rendered=self.processor.apply_chat_template([{'role':'user','content':content}],tokenize=False,add_generation_prompt=True)
        count=len(frames);span=timestamps[-1]-timestamps[0]
        metadata={'fps':(count-1)*1000/span,'total_num_frames':count,'duration':span/1000,'frames_indices':list(range(count))}
        extra={'images':[detail_rgb],'images_kwargs':{'size':{'shortest_edge':CONFIG['minimum_pixels'],'longest_edge':CONFIG['maximum_pixels']}}} if detail_rgb is not None else {}
        return self.processor(text=[rendered],videos=[np.asarray(frames)],return_tensors='pt',**extra,
                              videos_kwargs={'do_sample_frames':False,'video_metadata':[metadata],
                              'size':{'shortest_edge':count*CONFIG['minimum_pixels'],'longest_edge':count*CONFIG['maximum_pixels']},
                              'cap_pixels_per_frame':True}).to('cuda')
