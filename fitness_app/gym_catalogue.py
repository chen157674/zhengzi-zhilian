"""健身房扩展动作；器械变式由视觉确认，握法和负重不由骨架猜测。"""

# ID、中文名、器械、部位、运动族、英文视觉描述。
EXERCISES = (
    ('barbell_bench_press','杠铃卧推','杠铃','胸','bench','a person lying on a flat bench doing a barbell bench press'),
    ('dumbbell_bench_press','哑铃卧推','哑铃','胸','bench','a person lying on a flat bench pressing two dumbbells above the chest'),
    ('incline_dumbbell_press','上斜哑铃卧推','哑铃','胸','incline','a person reclining on an inclined bench pressing dumbbells above the chest'),
    ('machine_chest_press','器械推胸','器械','胸','chest_press','a person sitting upright on a chest press machine pushing handles forward'),
    ('pec_deck_fly','蝴蝶机夹胸','器械','胸','fly','a person sitting with back against a pec deck machine bringing both arms together in front of the chest'),
    ('dumbbell_chest_fly','哑铃夹胸飞鸟','哑铃','胸','lying_fly','a person lying on a flat bench doing dumbbell chest flyes with slightly bent elbows'),
    ('cable_chest_fly','绳索夹胸','绳索','胸','fly','a person standing between two cable pulleys bringing their hands together in front of their chest'),
    ('cable_triceps_pushdown','绳索三头下压','绳索','手臂','pushdown','a person standing at a cable machine extending elbows and pushing a triceps handle down toward the thighs'),
    ('overhead_dumbbell_extension','哑铃过顶臂屈伸','哑铃','手臂','extension','a person doing an overhead dumbbell triceps extension with the dumbbell behind their head'),
    ('reverse_pec_deck_fly','蝴蝶机反向飞鸟','器械','肩','reverse_fly','a person facing the chest pad of a reverse pec deck machine spreading their arms sideways'),
    ('dumbbell_shoulder_press','哑铃推肩','哑铃','肩','overhead','a person doing a dumbbell shoulder press lifting two dumbbells overhead'),
    ('cable_y_raise','绳索Y举','绳索','肩','y_raise','a person doing a cable Y raise lifting nearly straight arms diagonally overhead in a Y shape'),
    ('dumbbell_lateral_raise','哑铃侧平举','哑铃','肩','raise','a person standing and raising dumbbells out to the sides with almost straight arms'),
    ('bent_over_dumbbell_reverse_fly','俯身哑铃反向飞鸟','哑铃','肩','bent_fly','a person bent forward at the hips spreading dumbbells sideways in a reverse fly'),
    ('dumbbell_front_raise','哑铃前平举','哑铃','肩','raise','a person standing and lifting dumbbells forward to shoulder height with almost straight arms'),
    ('assisted_pull_up','器械辅助引体向上','器械','背','pull_up','a person kneeling on the assistance platform of an assisted pull up machine pulling their body upwards'),
    ('pull_up','引体向上','徒手','背','pull_up','a person hanging from a fixed overhead pull up bar and pulling their body upwards'),
    ('lat_pulldown','高位下拉','器械','背','pulldown','a person sitting on a lat pulldown machine pulling an overhead cable bar down towards the upper chest'),
    ('seated_cable_row','坐姿绳索划船','绳索','背','row','a person sitting at a low cable row machine pulling the cable handle toward their abdomen'),
    ('cable_face_pull','绳索面拉','绳索','背','face_pull','a person pulling a cable rope toward their face with elbows spread out to the sides'),
    ('hammer_curl','哑铃锤式弯举','哑铃','手臂','curl','a person doing dumbbell hammer curls with a neutral palms facing each other grip'),
)
BY_ID = {row[0]: dict(zip(('action_id','name','equipment','body_group','family','prompt'),row)) for row in EXERCISES}
ACTION_IDS = tuple(BY_ID)
UPPER = [[11,13,15,23],[12,14,16,24]]
REQUIRED = {action: UPPER for action in ACTION_IDS}
ALIASES = {'pec_deck_fly':['蝴蝶机','夹胸','蝴蝶夹胸'], 'cable_triceps_pushdown':['三头下压','正握下压','反握下压'],
           'reverse_pec_deck_fly':['反向蝴蝶机'], 'dumbbell_shoulder_press':['哑铃肩推'],
           'cable_y_raise':['Y举'], 'seated_cable_row':['坐姿划船']}


def definitions():
    return [(a['action_id'],a['name'],a['equipment'],'reps',None,[],
             '固定相机，肩、肘、腕和髋持续入镜；正面看左右同步，侧面看躯干摆动，器械需可见') for a in BY_ID.values()]


def rules():
    result={}
    for action,a in BY_ID.items():
        prefix=action+'.'
        if a['family'] not in {'bench','incline','lying_fly'}:
            result[prefix+'trunk_sway']=(action,'运动过程中躯干摆动较大',['肩','髋'],['side','oblique'],18.,
                '尝试减少前后借力，保持躯干稳定，控制器械返回；必要时降低负重。主动摆动的专项变式不适用此项。')
        if a['family'] in {'bench','incline','chest_press','overhead','raise','y_raise','fly','lying_fly','reverse_fly','bent_fly','pulldown','face_pull'}:
            result[prefix+'arm_asymmetry']=(action,'双侧手臂高度持续不一致',['左肘','右肘','左腕','右腕'],['front','oblique'] if action=='pec_deck_fly' else ['front'],.25,
                '放慢动作，检查两侧手臂是否同步、座椅与身体是否摆正；单侧或交替练习请勿使用这项双侧判断。')
        if a['family'] in {'pushdown','curl','extension'}:
            result[prefix+'upper_arm_swing']=(action,'屈伸肘时上臂摆动较大',['肩','肘'],['side','oblique'],25.,
                '尝试保持上臂位置稳定，用可控制的节奏屈伸肘；不要靠肩部大幅摆动带动器械。')
    return result


RULES=rules()
CANDIDATE_RULES={'machine_chest_press.trunk_lateral_lean':('machine_chest_press','躯干持续向一侧偏斜',['躯干'],['front','oblique'],12.,
    '先暂停，检查身体是否坐正、两侧握持位置是否一致，再以可控制的节奏继续。当前为开发候选，不判断受力。')}
LIMITATIONS=['仅提示可见关节投影与躯干方向；不判断肌肉受力、疼痛、脊柱局部曲度、重量或握法',
             '双侧同步规则只适用于双侧同时动作；机位、器械遮挡和主动变式会限制判断',
             '图像阈值为工程参考，未通过独立教练标签验收；无提示不等于整体动作正确']
