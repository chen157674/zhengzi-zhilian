"""离线数据来源分区约束；按作者/原片防止预留或评估素材误入训练。"""
import json
from .paths import ROOT


def source_policies():
    path=ROOT/'data/datasets/development-movements/sources.json'
    sources=json.loads(path.read_text(encoding='utf-8'))['sources']
    plan=ROOT/'data/datasets/development-movements/cc-round3-source-selection.json'
    if plan.exists():sources+=prospective_holdout_policies(json.loads(plan.read_text(encoding='utf-8')))
    return sources


def prospective_holdout_policies(plan):
    """已核实并确认预留的作者不会因首个视频传输失败而失去保护；不伪造媒体登记。"""
    policies=[]
    for row in plan.get('videos',[]):
        if row.get('prospective_role')!='reserved_new_publisher_holdout' or row.get('primary_cc_verified') is not True or row.get('partition_review_before_media_download') is not True or row.get('model_inference_allowed_before_freeze') is not False:continue
        if not isinstance(row.get('group'),str) or not row['group'] or not isinstance(row.get('id'),str) or not row['id']:raise ValueError('已确认预留来源缺少作者或视频身份')
        policies.append({'id':'youtube-'+row['id'],'source_group':row['group'],'partition':'reserved_holdout','evaluation_only':True,
            'model_inference_allowed_before_freeze':False,'policy_only':True,'policy_origin':'confirmed_prospective_holdout'})
    return policies


def model_partition_roles():
    """查已登记/已消费分区及已确认预留；计划本身不能抹去任何已消费身份。"""
    roles={}
    def add(rows,forced=None):
        for row in rows:
            group=row.get('group') or row.get('source_group');partition=forced or row.get('partition')
            if isinstance(group,str) and group and partition in {'train','validation','diagnostic','reserved_holdout'}:roles.setdefault(group,set()).add(partition)
    add(source_policies())
    for relative,key in (('data/verification/step-22/semantic-video-features.json','entries'),
                         ('data/verification/round3/new-source-action-probe.json','rows'),
                         ('data/verification/round3/partial-view-action-probe.json','rows')):
        path=ROOT/relative
        if path.exists():add(json.loads(path.read_text(encoding='utf-8'))[key])
    path=ROOT/'data/verification/round3/domain-encoder-adapter-plan.json'
    if path.exists():add(json.loads(path.read_text(encoding='utf-8'))['training'],'train')
    return roles


def validate_prospective_role(group,role,roles):
    if not isinstance(group,str) or not group:raise ValueError('新来源缺少明确作者组')
    existing=set(roles.get(group,()))
    if role=='train':conflict=existing&{'validation','diagnostic','reserved_holdout'}
    elif role=='validation_supplement':conflict=existing&{'train','reserved_holdout'}
    elif role=='reserved_new_publisher_holdout':conflict=existing&{'train','validation','diagnostic'}
    else:raise ValueError('新来源前瞻角色无效')
    if conflict:raise ValueError('新来源角色与作者既有分区冲突: '+role+' / '+','.join(sorted(existing)))


def matches(entry,source):
    source_id=source.get('id');ids={entry.get('id'),entry.get('source_id')}
    alias_only=source.get('duplicate_of') and source.get('exclude_from_training') and not source.get('evaluation_only') and source.get('partition')!='reserved_holdout' and source.get('model_inference_allowed_before_freeze') is not False
    if alias_only:
        key=entry.get('id')
        return bool(source_id in ids or isinstance(source_id,str) and isinstance(key,str) and key.startswith(source_id+'-'))
    digest=source.get('sha256')
    groups={v for v in (source.get('source_group'),source.get('group')) if isinstance(v,str) and v}
    if source.get('restriction_scope')=='asset':groups=set()
    entry_groups={v for v in (entry.get('source_group'),entry.get('group')) if isinstance(v,str) and v}
    return bool(source_id in ids or digest and digest==entry.get('source_sha256') or groups&entry_groups)


def enforce_source_policy(entries,policies=None,operation='training'):
    if operation not in {'training','inference'}:raise ValueError('来源约束操作无效')
    policies=source_policies() if policies is None else policies
    for source in policies:
        reserved=source.get('partition')=='reserved_holdout' or source.get('model_inference_allowed_before_freeze') is False
        protected=reserved or source.get('evaluation_only') is True or source.get('exclude_from_training') is True
        if not protected:continue
        for entry in entries:
            denied=entry.get('partition')=='train' and protected if operation=='training' else reserved
            match_source={**source,'restriction_scope':'asset'} if operation=='inference' and source.get('inference_restriction_scope')=='asset' else source
            if denied and matches(entry,match_source):
                raise ValueError('受限来源不能用于当前'+('训练' if operation=='training' else '普通模型探针')+': '+str(source.get('id')))
