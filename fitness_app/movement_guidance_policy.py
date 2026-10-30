"""按动作限定的运动指导参考；独立开关，不开放未验证的握持结论。"""
import os


def enabled():return os.environ.get('FITNESS_MOVEMENT_GUIDANCE')=='1'


# 指标与用力方向；肘角增大为伸肘，肩角增大为抬臂。
CYCLE_SPECS={
    'machine_chest_press':('elbow',1),'barbell_bench_press':('elbow',1),
    'dumbbell_bench_press':('elbow',1),'incline_dumbbell_press':('elbow',1),
    'dumbbell_shoulder_press':('elbow',1),'barbell_overhead_press':('elbow',1),
    'cable_triceps_pushdown':('elbow',1),'overhead_dumbbell_extension':('elbow',1),
    'standing_dumbbell_curl':('elbow',-1),'hammer_curl':('elbow',-1),
    'lat_pulldown':('elbow',-1),'seated_cable_row':('elbow',-1),
    'pull_up':('elbow',-1),'assisted_pull_up':('elbow',-1),
    'dumbbell_lateral_raise':('shoulder',1),'dumbbell_front_raise':('shoulder',1),'cable_y_raise':('shoulder',1),
}
SYNC_SPECS={a:v[0] for a,v in CYCLE_SPECS.items() if a not in
            {'standing_dumbbell_curl','hammer_curl','cable_triceps_pushdown','overhead_dumbbell_extension'}}
SYNC_SPECS.update({'pec_deck_fly':'reach','reverse_pec_deck_fly':'reach','cable_chest_fly':'reach','cable_face_pull':'reach'})
ELBOW_ACTIONS={'standing_dumbbell_curl','hammer_curl','cable_triceps_pushdown','overhead_dumbbell_extension'}
WRIST_ACTIONS=set(CYCLE_SPECS)|{'pec_deck_fly','reverse_pec_deck_fly','cable_chest_fly','cable_face_pull'}
RULES={}
for action in sorted(set(SYNC_SPECS)|ELBOW_ACTIONS|WRIST_ACTIONS):
    if action in SYNC_SPECS:
        RULES[action+'.phase_lag']=(action,'两侧动作阶段持续不同步',['左肘','右肘','左腕','右腕'],['front','oblique'],250.,
            '放慢动作，让两侧在相近时间完成推拉和返回；主动单侧或交替练习不使用这项判断。')
    if action in ELBOW_ACTIONS:
        RULES[action+'.elbow_drift']=(action,'肘部相对躯干位置明显移动',['肩','肘','髋'],['side','oblique'],.18,
            '尝试固定上臂和肘部位置，再完成屈伸；先用更容易控制的负重练习。')
    if action in CYCLE_SPECS:
        RULES[action+'.fast_return']=(action,'回程节奏明显快于用力阶段',['肩','肘','腕'],['front','side','oblique'],450.,
            '尝试平稳控制回程，避免突然甩回；有意快速训练时以训练计划为准。')
    if action in WRIST_ACTIONS:
        RULES[action+'.wrist_bend']=(action,'可见手腕持续偏折较明显',['肘','腕','手掌'],['front','side','oblique'],30.,
            '检查手腕与前臂是否自然对齐，避免明显折腕；看不清手掌时先暂停确认。')

LIMITATIONS=['动作阶段和关节关系为可见投影参考，未完成独立具体错误准确率验收',
             '主动交替、单侧或特殊训练节奏需按训练计划判断；看不清时保持无法评估',
             '手腕偏折不等于确认握力、手掌接触或拇指握法；速度不等于实际失控']
