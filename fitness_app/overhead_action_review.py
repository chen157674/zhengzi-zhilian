"""过顶运动的单次视觉快速候选；未通过实际对照前不接生产。"""
import hashlib,json,time
import numpy as np
from .pull_press_review import route
from .paths import ROOT,project_path
from .video_vlm import CODES,LABELS,CONFIG
from .compact_video import CompactVideoVLM,DESCRIPTIONS

ACTIONS=('lat_pulldown','machine_chest_press','reverse_pec_deck_fly','dumbbell_shoulder_press',
    'barbell_overhead_press','assisted_pull_up','pull_up','overhead_dumbbell_extension',
    'cable_y_raise','jumping_jack','barbell_bench_press','dumbbell_bench_press','incline_dumbbell_press')
POLICY={'source_gate':'original_conflicting_overhead_geometry_without_action_label',
    'minimum_score':.9,'minimum_margin':.5,'minimum_elbow_range_degrees':25.,'maximum_elbow_median_degrees':155.,'only_shortcut_action':'lat_pulldown',
    'production_enabled':False,'weight_training':False}
PROMPT=(
    'Observe the actual moving person and equipment. Select the exercise in these frames. '
    'Lat pulldown: a seated person pulls an OVERHEAD handle DOWN toward upper chest, returns UP; '
    'the torso stays seated while arms move overhead and down. '
    'Chest press: handles pushed FORWARD from chest height, not pulled down from overhead. '
    'Shoulder press: barbell or separate dumbbells pushed UP from shoulders. '
    'Triceps extension: upper arms remain near the head, weight moves BEHIND head. '
    'Pull-up: BODY moves toward a fixed bar, not pulling a handle down to a stationary seated torso. '
    'Bench press lies on a bench. Y raise uses nearly straight arms lifting diagonally. '
    'Do not infer from captions. Imperfect technique still belongs to its exercise. '
    'Choose 0 for rest, setup, static grip without exercise movement, none of the listed actions, or unclear. '
    'Reply ONE listed code only.\n'
)


def version():
    return 'overhead-fast-'+hashlib.sha256(json.dumps({'policy':POLICY,'prompt':PROMPT,'actions':ACTIONS},sort_keys=True).encode()).hexdigest()[:12]


def eligible(facts):
    values=[facts.get('elbow_range_deg'),facts.get('elbow_median_deg')]
    if any(type(v) not in (int,float) or not np.isfinite(v) for v in values):return False
    return bool(route({'action_id':'machine_chest_press'},facts) and
        values[0]>=POLICY['minimum_elbow_range_degrees'] and values[1]<=POLICY['maximum_elbow_median_degrees'])


def release_policy(info):
    path=ROOT/'data/models/overhead-fast-review.json'
    if not path.exists():return None
    document=json.loads(path.read_text(encoding='utf-8'))
    if not document.get('runtime_enabled'):return None
    if (document['version']!=version() or document['base_model_version']!=info['version'] or
        document['base_head_sha256']!=info['sha256'] or
        document['code_sha256']!=hashlib.sha256(__import__('pathlib').Path(__file__).read_bytes()).hexdigest()):
        raise ValueError('实时过顶快速复核身份不一致')
    report_path=project_path(document['evaluation_path']);raw=report_path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=document['evaluation_sha256']:raise ValueError('实时过顶验证结果变化')
    report=json.loads(raw)
    if report['state']!='completed' or report['version']!=version() or not report['safe_nonregression_candidate']:
        raise ValueError('实时过顶复核尚未通过开发门禁')
    if document.get('execution_version')=='stream1':
        raw=project_path(document['execution_evaluation_path']).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=document['execution_evaluation_sha256']:
            raise ValueError('实时执行策略验证摘要变化')
        result=json.loads(raw)
        import inspect
        signature=hashlib.sha256((inspect.getsource(OverheadActionReviewer.review)+inspect.getsource(OverheadActionReviewer.prepare_inputs)).encode()).hexdigest()
        if (result.get('state')!='completed' or not result.get('numeric_gate_passed') or
            result.get('score_implementation_sha256')!=signature or result['all_class_max_abs_error']>1e-5):
            raise ValueError('实时执行策略与数值验证不一致')
    return document


class OverheadActionReviewer(CompactVideoVLM):
    def __init__(self,base,low_latency=False):
        self.base=base;self.processor=self.model=self.code_ids=None;self.low_latency=low_latency
        self.version=version()

    def review(self,images,times):
        self.base.load();self.model,self.processor,self.code_ids=self.base.model,self.base.processor,self.base.code_ids
        if not self.low_latency:return self.predict(images,times)
        # 与CompactVideoVLM相同的输入／分数计算；实时路径不清全局allocator缓存、不等全部CUDA流。
        import torch
        if len(images)!=CONFIG['frames'] or len(times)!=CONFIG['frames'] or any(b<=a for a,b in zip(times,times[1:])):
            raise ValueError('实时过顶复核需要八张递增源图')
        if not CONFIG['minimum_span_ms']<=times[-1]-times[0]<=CONFIG['maximum_span_ms']:
            raise ValueError('实时过顶复核时间范围无效')
        if any(frame.ndim!=3 or frame.shape[2]!=3 or frame.dtype!=np.uint8 for frame in images):
            raise ValueError('实时过顶复核需要RGB uint8')
        began=time.perf_counter();batch=self.prepare_inputs(images,times)
        with torch.inference_mode():
            output=self.model(**batch,logits_to_keep=1,use_cache=False)
            probabilities=output.logits[0,-1].float().softmax(-1)[self.code_ids].cpu().numpy()
        order=np.argsort(probabilities)[::-1];top=int(order[0]);score=float(probabilities[top])
        margin=score-float(probabilities[order[1]]);mass=float(probabilities.sum())
        accepted=score>=CONFIG['minimum_score'] and margin>=CONFIG['minimum_margin'] and mass>=CONFIG['minimum_code_mass']
        return {'action_id':LABELS[top] if accepted else None,'top_action_id':LABELS[top],
            'score':score,'margin':margin,'code_mass':mass,'scores':dict(zip([a or 'unknown' for a in LABELS],map(float,probabilities))),
            'model_version':self.version,'elapsed_ms':round((time.perf_counter()-began)*1000,1),
            'processed_video_grid_thw':batch['video_grid_thw'].cpu().tolist(),'runtime_enabled':False,
            'score_is_calibrated_probability':False,'execution_policy':'no_global_cache_flush_or_device_synchronize'}

    def prepare_inputs(self,frames,timestamps,observed=None,detail_rgb=None):
        choices=dict(zip(LABELS,CODES))
        text=PROMPT+'0: none/unknown\n'+'\n'.join(choices[a]+': '+DESCRIPTIONS[a] for a in ACTIONS)+'\nCode:'
        rendered=self.processor.apply_chat_template([{'role':'user','content':[{'type':'video'},{'type':'text','text':text}]}],
            tokenize=False,add_generation_prompt=True)
        count=len(frames);span=int(timestamps[-1]-timestamps[0])
        metadata={'fps':(count-1)*1000/span,'total_num_frames':count,'duration':span/1000,'frames_indices':list(range(count))}
        return self.processor(text=[rendered],videos=[np.asarray(frames)],return_tensors='pt',
            videos_kwargs={'do_sample_frames':False,'video_metadata':[metadata],
                'size':{'shortest_edge':count*64*64,'longest_edge':count*384*384},'cap_pixels_per_frame':True}).to('cuda')


def accepted_pulldown(result):
    return bool(result.get('action_id')=='lat_pulldown' and
        type(result.get('score')) in (int,float) and type(result.get('margin')) in (int,float) and
        np.isfinite([result['score'],result['margin']]).all() and
        result['score']>=POLICY['minimum_score'] and result['margin']>=POLICY['minimum_margin'])
