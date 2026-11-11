"""显式开启的预训练参考观察；默认不改变正式规则集合。"""
import os


def enabled():return os.environ.get('FITNESS_PRETRAINED_CORRECTION')=='1'


def body_enabled():return enabled() or os.environ.get('FITNESS_BODY_LEAN')=='1'


def active_rules():
    if enabled():return RULES
    return {k:v for k,v in RULES.items() if k.endswith('upper_body_balance')} if body_enabled() else {}


RULES={
    'machine_chest_press.pretrained_upper_body_balance':('machine_chest_press','上身持续向一侧歪斜',['躯干','肩','髋'],['front','oblique'],6.,
        '检查坐姿和座椅位置，让肩髋保持稳定对齐；先减轻负重，以可控制的动作继续。'),
    'lat_pulldown.pretrained_upper_body_balance':('lat_pulldown','下拉时躯干持续向一侧倾斜',['躯干','肩','髋'],['front','oblique'],6.,
        '先坐正并稳定骨盆，让躯干保持在座椅中间，再以可控制的负重下拉；避免持续向一侧借力。'),
    'machine_chest_press.pretrained_finger_extension':('machine_chest_press','动作中手指持续张开',['手','腕'],['front','oblique'],2.,
        '检查是否握稳把手，保持可控制的闭合握持；看不清接触位置时先暂停确认。'),
    'barbell_bench_press.pretrained_posture':('barbell_bench_press','预训练模型观察到卧推姿态偏差',['躯干','肩','肘','腕'],['front','side','oblique'],.8,
        '检查握距、手臂与腰背位置，先用可控制的负重确认姿势。'),
    'conventional_deadlift.pretrained_posture':('conventional_deadlift','预训练模型观察到硬拉姿态偏差',['躯干','髋','膝','腕'],['front','side','oblique'],.8,
        '检查握距和躯干位置，按所用硬拉变式控制动作；不清楚时先暂停确认。'),
    'bodyweight_squat.pretrained_posture':('bodyweight_squat','预训练模型观察到深蹲姿态偏差',['躯干','髋','膝','踝'],['front','side','oblique'],.8,
        '检查膝盖方向、站距和躯干位置，以可控制的幅度与节奏继续。'),
}

LIMITATIONS=['本机预训练模型提供可见投影参考，尚无独立新人物纠错准确性结论',
             '手指伸展不等于已经确认手掌与器械接触，未知状态不能视为握法正确']
