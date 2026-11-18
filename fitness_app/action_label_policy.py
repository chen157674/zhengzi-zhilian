"""区分动作族标签与排他握法标签；不按模型答案改写原数据类别。"""
import math
from collections import Counter,defaultdict
CURL_ACTIONS=('standing_dumbbell_curl','hammer_curl')
VERSION='action-label-specificity-1'


def scope(entry):
    action=entry['action_id']
    if entry.get('evaluation_only') and entry.get('partition')=='train':raise ValueError('请选择训练来源')
    if entry.get('exclude_from_training') and entry.get('partition')=='train':raise ValueError('请选择新的训练来源')
    if action=='standing_dumbbell_curl' and not entry.get('fine_action_verified'):
        return {'specificity':'family','family':'dumbbell_curl','allowed_actions':list(CURL_ACTIONS),'leaf_label':None,
            'reason':'generic bicep_curl label does not establish supination or exclude neutral grip'}
    return {'specificity':'leaf','family':'dumbbell_curl' if action in CURL_ACTIONS else action,'allowed_actions':[action],'leaf_label':action,
        'reason':'publisher subtype and bounded source review' if entry.get('fine_action_verified') else 'original specific action label; no independent posture-error truth'}


def route(primary,subtype,score,margin,minimum=.8,minimum_margin=.25):
    result=dict(primary)
    if primary.get('action_id') not in CURL_ACTIONS:return result
    if not all(type(v) in (int,float) and math.isfinite(v) and 0<=v<=1 for v in (score,margin,minimum,minimum_margin)):return result
    if subtype not in CURL_ACTIONS or score<minimum or margin<minimum_margin:return result
    result.update(action_id=subtype,candidate_action_id=subtype,top_action_id=subtype,score=float(score),margin=float(margin),reason=None,
                  subtype_base_action_id=primary['action_id'],subtype_base_score=primary.get('score'),
                  decision_basis='experimental_curl_subtype_candidate',score_is_calibrated_probability=False,runtime_enabled=False)
    return result


def reviewed_scope(entry,review,cache_sha256):
    """复核只绑定确切输入；保留官方动作族，拒绝拿别的裁剪/时段的结论来细分。"""
    original=scope(entry)
    if entry['action_id'] not in CURL_ACTIONS:raise ValueError('请核对复核动作类型')
    for key in ('id','source_sha256','start_ms','end_ms','crop'):
        if review.get(key)!=entry.get(key):raise ValueError('握法复核与原输入身份不一致')
    if review.get('semantic_cache_sha256')!=cache_sha256:raise ValueError('握法复核特征缓存已变化')
    if review.get('review_basis')!='developer_visual_exact_frames' or review.get('independent_error_truth') is not False:raise ValueError('复核来源等级无效')
    stamps=review.get('actual_timestamps_ms')
    if not isinstance(stamps,list) or len(stamps)<3 or any(type(v) is not int or not entry['start_ms']<=v<entry['end_ms'] for v in stamps) or any(b<=a for a,b in zip(stamps,stamps[1:])):raise ValueError('复核缺少有序且范围内的实际画面时间')
    window=review.get('semantic_timestamps_ms')
    if not isinstance(window,list) or len(window)!=8 or any(type(v) is not int or not entry['start_ms']<=v<entry['end_ms'] for v in window) or any(b<=a for a,b in zip(window,window[1:])) or stamps[0]<window[0] or stamps[-1]>window[-1]:raise ValueError('复核画面不属于已缓存的语义窗口')
    if review.get('decision')=='exclude_unconfirmed_exercise':
        return {**original,'leaf_label':None,'reason':'exercise phase unconfirmed in exact reviewed semantic input','training_excluded':True}
    label=review.get('grip_label')
    if review.get('decision')!='verified_subtype' or label not in CURL_ACTIONS:raise ValueError('握法复核结论无效')
    return {'specificity':'leaf','family':'dumbbell_curl','allowed_actions':[label],'leaf_label':label,
            'reason':'developer review of bounded original frames; not independent technique truth','training_excluded':False}


def group_balanced_weights(rows):
    """每类、来源组、原片依次等权；重复裁剪不能增加同来源的总权重。"""
    if not rows:raise ValueError('缺少训练输入')
    clips=Counter();groups=defaultdict(set);originals=defaultdict(set)
    for row in rows:
        action,group,digest=row['expected'],row['group'],row['source_sha256']
        if action not in CURL_ACTIONS or not isinstance(group,str) or not group or not isinstance(digest,str) or not digest:raise ValueError('训练权重缺少类别/来源身份')
        groups[action].add(group);originals[(action,group)].add(digest);clips[(action,group,digest)]+=1
    weights=[1/(len(groups[r['expected']])*len(originals[(r['expected'],r['group'])])*clips[(r['expected'],r['group'],r['source_sha256'])]) for r in rows]
    factor=len(weights)/sum(weights)
    return [w*factor for w in weights]
