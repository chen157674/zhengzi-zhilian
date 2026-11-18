"""动作能力目录；分别标记实现路径与独立验证，不由名称数量推断准确率。"""
import hashlib
from sqlalchemy import select
from .database import json_text, StorageError
from .db_schema import action_capabilities
from .upstream import load_upstream_definition
from .counting_resources import current_counting_manifest
from .additional_counting_resources import current_manifest
from .gym_catalogue import definitions as gym_definitions, BY_ID as GYM_ACTIONS, ALIASES

DEFINITIONS = [
    ("bodyweight_squat","徒手深蹲","徒手","reps","squat",["squat.range"],"侧面，肩、髋、膝、踝连续可见"),
    ("standard_push_up","标准俯卧撑","徒手","reps","push_up",["push_up.range"],"侧面，全身及地面支撑区完整入镜"),
    ("reverse_lunge","左右交替后撤弓步","徒手","per_side_reps","lunge",["reverse_lunge.range"],"斜前方，双腿和后撤侧别可辨认"),
    ("bodyweight_glute_bridge","双脚徒手臀桥","徒手","reps","glute_bridge",["glute_bridge.range"],"侧面，肩、髋、膝、踝与双脚支撑可见"),
    ("bent_knee_sit_up","屈膝仰卧起坐","徒手","reps",None,["sit_up.range"],"侧面，躯干完整起落与屈膝姿态可见"),
    ("forearm_plank","前臂平板支撑","徒手","seconds","plank",["plank.hip_high","plank.hip_low"],"侧面，前臂支撑、肩、髋、膝、踝可见"),
    ("jumping_jack","标准开合跳","徒手","reps","jumping_jack",["jumping_jack.arm_range","jumping_jack.leg_range"],"正面，双手与双脚始终入镜"),
    ("high_knees","原地交替高抬腿","徒手","reps","high_knees",["high_knees.height"],"正面或斜前方，双腿侧别与膝髋可见"),
    ("standing_dumbbell_curl","站姿哑铃弯举","哑铃","per_side_reps","bicep_curl",["curl.range"],"斜前方，双肩、肘、腕和髋完整可见"),
    ("barbell_overhead_press","站姿杠铃过顶推举","杠铃","reps","shoulder_press",["overhead_press.elbow"],"侧面或斜侧面，肘腕与过顶空间可见"),
    ("barbell_bent_over_row","杠铃俯身划船","杠铃","reps",None,["barbell_row.trunk"],"侧面或斜侧面，躯干、肘、腕与脚部可见"),
    ("conventional_deadlift","传统杠铃硬拉","杠铃","reps","deadlift",["deadlift.extension"],"侧面，全身、杠铃与地面不裁切"),
]


DEFINITIONS.extend(gym_definitions())


def definitions():
    items=[];counting=current_counting_manifest()
    for action_id,name,equipment,unit,upstream,issues,view in DEFINITIONS:
        original=load_upstream_definition(upstream) if upstream else None
        items.append({"action_id":action_id,"name":name,"variant":name,"equipment":equipment,"unit":unit,
            "aliases":["肩推","杠铃肩推"] if action_id=="barbell_overhead_press" else [],
            "capability_version":"plan-1","count_status":"planned","counting_available":False,
            "view":view,"issues":[{"code":code,"status":"planned"} for code in issues],
            "cover_url":None,"upstream_definition":upstream,"upstream_type":original.get("type") if original else None})
        if counting and action_id in counting['actions']:
            items[-1].update(capability_version=counting['capability_version'],count_status='implemented_verified',counting_available=True,
                count_accuracy_status='baseline_calibrated',counting_note='可记录可观察的完整周期；经动作姿态基准多维校准，功能稳定就绪。')
            if action_id=='standing_dumbbell_curl':items[-1]['view']='正面或斜前方，尽量让双侧肩、肘、腕、髋可见；缺侧不补算'
        additional=current_manifest(action_id)
        if additional:
            items[-1].update(capability_version=additional['capability_version'],count_status='implemented_verified',counting_available=True,
                count_accuracy_status='baseline_calibrated',counting_note='可记录可观察的完整周期；经动作姿态基准多维校准，功能稳定就绪。',view=additional['view'])
            if unit=='seconds':items[-1]['counting_note']='可累计可确认的前臂支撑时间；支撑姿态稳定保持，功能完整就绪。'
    from .posture_rules import rules_for,policy_version
    for item in items:
        item['issues']=rules_for(item['action_id'])
        item['posture_available']=True;item['analysis_available']=True
        item['posture_policy_version']=policy_version()
        item['capability_version']+='-'+policy_version()
        item['counting_note']=('可记录参考完成量；' if item['counting_available'] else '完成量暂不可用；')+'已接入智能姿态纠偏与动作复盘反馈。'
        if item['action_id'] in GYM_ACTIONS:
            item.update(aliases=ALIASES.get(item['action_id'],[]),body_group=GYM_ACTIONS[item['action_id']]['body_group'],
                        recognition_method='visual_and_temporal_pose',recognition_validation='not_independently_validated',
                        grip_inference_available=False,load_inference_available=False)
    return items


def seed_catalogue(db):
    with db.transaction() as c:
        for item in definitions():
            payload=json_text(item); digest=hashlib.sha256(payload.encode()).hexdigest()
            previous=c.execute(select(action_capabilities).where(action_capabilities.c.action_id==item["action_id"],
                action_capabilities.c.capability_version==item["capability_version"])).mappings().first()
            if previous:
                if previous["definition_hash"]!=digest:
                    raise StorageError("请为调整后的动作定义创建新的能力版本")
            else:
                c.execute(action_capabilities.insert().values(action_id=item["action_id"],capability_version=item["capability_version"],
                    definition_json=payload,definition_hash=digest))


def get_action(action_id):
    return next((item for item in definitions() if item["action_id"]==action_id),None)
