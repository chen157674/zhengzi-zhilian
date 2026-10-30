"""在少量易混淆动作内复核器械与运动方向；共享本地视觉模型，不生成纠错真值。"""
import hashlib,json
import numpy as np
from .compact_video import CompactVideoVLM,DESCRIPTIONS
from .video_vlm import CONFIG,LABELS,CODES

FAMILIES={
 'curl':('standing_dumbbell_curl','hammer_curl','dumbbell_front_raise'),
 'cable':('cable_y_raise','cable_chest_fly','cable_face_pull','cable_triceps_pushdown'),
 'chest':('barbell_bench_press','dumbbell_bench_press','incline_dumbbell_press','dumbbell_chest_fly','machine_chest_press','pec_deck_fly','reverse_pec_deck_fly'),
 'shoulder':('barbell_overhead_press','dumbbell_shoulder_press','overhead_dumbbell_extension'),
 'hinge':('barbell_bent_over_row','conventional_deadlift','bent_over_dumbbell_reverse_fly'),
 'pull':('seated_cable_row','lat_pulldown','assisted_pull_up','pull_up'),
 'lower':('bodyweight_squat','reverse_lunge','high_knees','jumping_jack'),
 'floor':('standard_push_up','forearm_plank','bodyweight_glute_bridge','bent_knee_sit_up'),
}
CONTRAST={
 'curl':'Hammer curl: palms face each other, thumbs up throughout. Biceps curl: palms face forward/up or rotate upwards. Front raise: shoulder lifts nearly straight arms; elbow angle changes little. Inspect the actual hands and joint motion.',
 'cable':'Y raise: near-straight arms rise diagonally OVERHEAD. Chest fly: arms converge IN FRONT OF CHEST. Face pull: elbows bend as rope reaches face/ears. Triceps pushdown: upper arms at sides, hands push DOWN. Equipment in background alone does not establish an exercise.',
 'chest':'Flat versus incline: inspect the bench backrest and torso angle. Fly opens/closes arms sideways with nearly fixed elbow angle; press bends/extends elbows. Machine chest press pushes handles forwards; pec deck closes arms together. Reverse pec deck has CHEST FACING the pad, unlike chest fly.',
 'shoulder':'Shoulder press moves upper arms upwards from shoulder level; triceps extension keeps upper arms by the head and bends elbows with weight BEHIND the head. Distinguish a single barbell from independent dumbbells.',
 'hinge':'Deadlift: bar rises from floor as hips/knees extend to standing. Barbell row: torso stays bent forward, elbows pull bar to abdomen. Rear delt fly: bent torso, independent dumbbells spread out sideways.',
 'pull':'Pull-up moves the BODY to a fixed bar; lat pulldown moves the HANDLE down to a seated person. Assisted pull-up requires the machine support platform. Seated row pulls a low/front cable to waist, not from overhead.',
 'lower':'Reverse lunge steps BACKWARDS. Squat keeps both feet planted. High knees alternates lifting knees while upright. Jumping jack spreads arms and legs together. Weighted forward lunge and walking lunge are not the listed bodyweight reverse lunge.',
 'floor':'Forearm plank is a static hold on forearms. Push-up supports on palms and bends elbows. Glute bridge is supine hip lifting. Sit-up lifts the trunk from supine with bent knees.',
}
CODE_BY_ACTION=dict(zip(LABELS,CODES))
BODY_ACTIONS=set(FAMILIES['lower']+FAMILIES['floor'])
POLICY={'schema':'visual-family-review-1','minimum_score':.75,'minimum_margin':.25,'strong_background':.7,
        'always_review':('curl','cable','chest'),'shoulder_disagreement':True,'rescue_strong_background':'only listed bodyweight action with visual support'}


def family_prompt(family):
    return ('Inspect the actual movement and equipment in this video, ignoring captions. '+CONTRAST[family]+
            ' Nonstandard technique still belongs to its exercise. Choose 0 if none fits, only talking/setup/rest, or evidence is insufficient. '
            'Only sitting at a machine without gripping and moving its handles is NOT training. Reply ONE code only.\n0: none/unknown\n'+
            '\n'.join(CODE_BY_ACTION[a]+': '+DESCRIPTIONS[a] for a in FAMILIES[family])+'\nCode:')


def policy_version():
    value={'policy':POLICY,'families':FAMILIES,'prompts':{k:family_prompt(k) for k in FAMILIES}}
    return 'family-review-'+hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()[:12]


def family_for(action):return next((name for name,actions in FAMILIES.items() if action in actions),None)


def choose_family(primary,visual):
    action=primary['action_id'];family=family_for(action)
    if family in POLICY['always_review']:return family
    if family in ('shoulder','hinge','pull') and (primary['score']<.8 or action!=visual['top_action_id']):return family
    if action is not None:return None
    alternative=visual['top_action_id']
    if alternative is not None and visual['score']>=.8:return family_for(alternative)
    if primary.get('top_action_id') is None and primary['score']<.7:
        for alternative,score in primary.get('candidates',[]):
            if alternative!='unknown' and score>=.1:return family_for(alternative)
    return None


def combine(primary,review,family):
    result=dict(primary);result['refinement_family']=family;result['refinement_action_id']=review['action_id']
    action=review['action_id'];accepted=review['score']>=POLICY['minimum_score'] and review['margin']>=POLICY['minimum_margin']
    strong_background=primary.get('top_action_id') is None and primary['score']>=POLICY['strong_background']
    if accepted and action in FAMILIES[family] and (not strong_background or action in BODY_ACTIONS):
        result.update(action_id=action,candidate_action_id=action,top_action_id=action,score=review['score'],margin=review['margin'],reason=None,
                      decision_basis='trained_video_with_visual_family_review')
    elif accepted and action is None and review.get('top_action_id') is None and primary['score']<.85:
        result.update(action_id=None,candidate_action_id=None,top_action_id=None,score=review['score'],margin=review['margin'],reason='video_family_uncertain')
    return result


class VideoFamilyRefiner(CompactVideoVLM):
    def __init__(self,base):
        # 使用同一个已经加载的基座，不能另占一份4B显存。
        self.base=base;self.model=self.processor=self.code_ids=None;self.family=None
        self.version=base.version+'-'+policy_version()

    def review(self,images,timestamps,family,observed=None,detail_rgb=None):
        self.base.load();self.model,self.processor,self.code_ids=self.base.model,self.base.processor,self.base.code_ids
        self.family=family
        result=super().predict(images,timestamps,observed,detail_rgb)
        if result['action_id'] not in (None,*FAMILIES[family]):
            result.update(action_id=None,reason='family_code_outside_choices')
        return result

    def prepare_inputs(self,frames,timestamps,observed=None,detail_rgb=None):
        text=family_prompt(self.family)
        if observed:text+='\nApproximate projected joint observations (can be inaccurate): '+json.dumps(observed,sort_keys=True)
        content=[{'type':'video'}]
        if detail_rgb is not None:content.append({'type':'image'});text+='\nThe still image shows the SAME person in closer detail.'
        content.append({'type':'text','text':text})
        rendered=self.processor.apply_chat_template([{'role':'user','content':content}],tokenize=False,add_generation_prompt=True)
        count=len(frames);span=timestamps[-1]-timestamps[0]
        metadata={'fps':(count-1)*1000/span,'total_num_frames':count,'duration':span/1000,'frames_indices':list(range(count))}
        extra={'images':[detail_rgb],'images_kwargs':{'size':{'shortest_edge':CONFIG['minimum_pixels'],'longest_edge':CONFIG['maximum_pixels']}}} if detail_rgb is not None else {}
        return self.processor(text=[rendered],videos=[np.asarray(frames)],return_tensors='pt',**extra,
            videos_kwargs={'do_sample_frames':False,'video_metadata':[metadata],
                          'size':{'shortest_edge':count*CONFIG['minimum_pixels'],'longest_edge':count*CONFIG['maximum_pixels']},
                          'cap_pixels_per_frame':True}).to('cuda')
