"""过顶轨迹与胸推标签冲突的跨组候选；需独立复核后再接生产。"""
import hashlib
import json
import numpy as np
from .paths import ROOT,project_path

ACTIONS=('lat_pulldown','machine_chest_press','reverse_pec_deck_fly','dumbbell_shoulder_press',
    'barbell_overhead_press','assisted_pull_up','pull_up')
CONFIG={'minimum_source_frames':12,'minimum_coverage':.7,'minimum_visibility':.7,
    'minimum_span_ms':1200,'maximum_gap_ms':500,'wrist_above_shoulder_max_torso':.5,
    'upper_arm_median_degrees':100.,'upper_arm_excursion_degrees':60.,'minimum_score':.75,'minimum_margin':.25,
    'only_route_accepted_chest_actions':True,'production_enabled':False}
PROMPT=(
    'Classify the ACTUAL exercise from motion and equipment across all frames. '
    'Compare the direction of the handles relative to the torso. '
    'A seated LAT PULLDOWN has a cable or handle ABOVE the head, pulled DOWN toward the upper chest; '
    'elbows move from overhead down beside the torso and return up. '
    'A MACHINE CHEST PRESS pushes handles FORWARD from chest height and returns, not from above the head. '
    'REVERSE PEC DECK opens the arms BACKWARDS with the chest facing its pad; the elbows do not cycle overhead. '
    'A SHOULDER PRESS pushes a weight UP from shoulder height to overhead. '
    'PULL-UP moves the BODY toward a fixed bar rather than pulling a handle down to a stationary seated torso. '
    'Use visible apparatus and movement, not the color of clothing or background text. '
    'Imperfect technique stays the same exercise. Setup, rest, unsupported action or uncertain evidence is 0. '
    'Only choose one code from the provided list; no explanation.\n'
)


def version():
    return 'pull-press-review-'+hashlib.sha256(json.dumps({'config':CONFIG,'prompt':PROMPT,'actions':ACTIONS},sort_keys=True).encode()).hexdigest()[:12]


def route(decision,facts):
    if decision.get('action_id') not in {'machine_chest_press','reverse_pec_deck_fly'}:return False
    high=facts.get('wrist_above_shoulder_max_torso');arm=facts.get('upper_arm_to_trunk_median_deg');excursion=facts.get('upper_arm_range_deg')
    def finite(value):return type(value) in (int,float) and np.isfinite(value)
    return bool(finite(high) and high>=CONFIG['wrist_above_shoulder_max_torso'] and
        (finite(arm) and arm>=CONFIG['upper_arm_median_degrees'] or
         finite(excursion) and excursion>=CONFIG['upper_arm_excursion_degrees']))


def apply_review(decision,review,facts):
    """只改明确过顶冲突，模型置信不足或目录外结论不改原类别。"""
    result=dict(decision)
    if not route(decision,facts):return result
    action=review.get('action_id');score=review.get('score');margin=review.get('margin')
    if (review.get('accepted') is not True or action not in (None,*ACTIONS) or
        any(type(v) not in (int,float) or not np.isfinite(v) or not 0<=v<=1 for v in (score,margin)) or
        score<CONFIG['minimum_score'] or margin<CONFIG['minimum_margin']):return result
    result.update(action_id=action,candidate_action_id=action,top_action_id=action,score=score,margin=margin,
        reason=None if action else 'cross_group_video_uncertain',
        decision_basis='trained_video_with_overhead_cross_review',
        cross_review={'version':version(),'baseline_action_id':decision['action_id'],
            'baseline_score':decision.get('score'),'action_id':action,'score':score,'margin':margin,
            'scope':'cross_group_action_review_not_posture_error','score_is_calibrated_probability':False})
    return result


def release_policy(info):
    """独立发布清单绑定原分类头与验证报告；缺失时保留原识别器。"""
    path=ROOT/'data/models/pull-press-review.json'
    if not path.exists():return None
    document=json.loads(path.read_text(encoding='utf-8'))
    if not document.get('runtime_enabled'):return None
    if (document['version']!=version() or document['base_model_version']!=info['version'] or
        document['base_head_sha256']!=info['sha256'] or
        document['code_sha256']!=hashlib.sha256(__import__('pathlib').Path(__file__).read_bytes()).hexdigest()):
        raise ValueError('跨组动作复核身份不一致')
    if document.get('runtime_code_sha256') and document['runtime_code_sha256']!=hashlib.sha256((ROOT/'fitness_app/semantic_model.py').read_bytes()).hexdigest():
        raise ValueError('跨组复核执行流程身份不一致')
    for item in document['reports']:
        raw=project_path(item['path']).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=item['sha256']:raise ValueError('跨组动作复核证据变化')
    if not document['development_nonregression_verified'] or document['independent_accuracy_passed'] is not False:
        raise ValueError('跨组动作复核发布状态不符')
    return document


class PullPressReviewer:
    def __init__(self,base):self.base=base;self.version=version()

    def review(self,images,timestamps,facts=None):
        import torch
        from .video_vlm import CODES,LABELS
        from .compact_video import DESCRIPTIONS
        self.base.load();processor=self.base.processor
        lookup=dict(zip(LABELS,CODES));allowed=[None,*ACTIONS];codes=[lookup[action] for action in allowed]
        text=PROMPT+'0: none/unknown\n'+'\n'.join(lookup[action]+': '+DESCRIPTIONS[action] for action in ACTIONS)+'\nCode:'
        # 完整视频用来核查实际器械；不把原分类结果／目标动作／错误标签写入提示。
        content=[{'type':'video'},{'type':'text','text':text}]
        rendered=processor.apply_chat_template([{'role':'user','content':content}],tokenize=False,add_generation_prompt=True)
        count=len(images);span=int(timestamps[-1]-timestamps[0])
        if count!=8 or not 1200<=span<=4000 or any(b<=a for a,b in zip(timestamps,timestamps[1:])):
            raise ValueError('跨组复核需要八张连续范围内真实画面')
        metadata={'fps':(count-1)*1000/span,'total_num_frames':count,'duration':span/1000,'frames_indices':list(range(count))}
        batch=processor(text=[rendered],videos=[np.asarray(images)],return_tensors='pt',
            videos_kwargs={'do_sample_frames':False,'video_metadata':[metadata],
                'size':{'shortest_edge':count*64*64,'longest_edge':count*384*384},'cap_pixels_per_frame':True}).to('cuda')
        with torch.inference_mode():
            logits=self.base.model(**batch,logits_to_keep=1,use_cache=False).logits[0,-1].float()
            full=logits.softmax(-1)
            encoded=[processor.tokenizer.encode(code,add_special_tokens=False) for code in codes]
            if any(len(value)!=1 for value in encoded):raise ValueError('跨组复核类别不是单token')
            scores=full[torch.tensor([value[0] for value in encoded],device='cuda')].cpu().numpy()
        order=np.argsort(scores)[::-1];top=int(order[0]);score=float(scores[top]);margin=score-float(scores[order[1]])
        accepted=score>=CONFIG['minimum_score'] and margin>=CONFIG['minimum_margin']
        return {'action_id':allowed[top] if accepted else None,'top_action_id':allowed[top],
            'accepted':accepted,'score':score,'margin':margin,'code_mass':float(scores.sum()),
            'scores':dict(zip([value or 'unknown' for value in allowed],map(float,scores))),
            'model_version':self.version,'production_enabled':False,'score_is_calibrated_probability':False}
