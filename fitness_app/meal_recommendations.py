"""已发布的三样搭配与可采用建议；不把推荐当实际进食。"""
from copy import deepcopy
from .auth import APIError

VERSION = 'three-foods-1'
MEALS = {'breakfast': '早餐', 'lunch': '午餐', 'dinner': '晚餐'}
SCENES = {'home': '自己做', 'canteen': '食堂', 'takeaway': '外卖'}
SLOTS = {'staple': '主食', 'protein': '蛋白质', 'vegetable': '蔬菜'}
FOODS = [
    {'id': 'rice', 'name': '米饭', 'slot': 'staple', 'amount': '按食欲取用', 'aliases': ['米饭']},
    {'id': 'toast', 'name': '全麦面包', 'slot': 'staple', 'amount': '按食欲取用', 'aliases': ['面包', '小麦']},
    {'id': 'sweet-potato', 'name': '红薯', 'slot': 'staple', 'amount': '按食欲取用', 'aliases': ['红薯']},
    {'id': 'tomato-egg', 'name': '番茄炒蛋', 'slot': 'protein', 'amount': '一道家常菜', 'aliases': ['番茄', '鸡蛋']},
    {'id': 'tofu', 'name': '豆腐', 'slot': 'protein', 'amount': '搭配一份', 'aliases': ['豆腐', '大豆', '豆类']},
    {'id': 'fish', 'name': '鱼肉', 'slot': 'protein', 'amount': '搭配一份', 'aliases': ['鱼肉', '鱼', '海鲜']},
    {'id': 'chicken', 'name': '鸡肉', 'slot': 'protein', 'amount': '搭配一份', 'aliases': ['鸡肉', '鸡胸肉']},
    {'id': 'boiled-eggs', 'name': '鸡蛋', 'slot': 'protein', 'amount': '搭配一份', 'aliases': ['鸡蛋', '蛋类']},
    {'id': 'leafy-greens', 'name': '清炒青菜', 'slot': 'vegetable', 'amount': '搭配蔬菜', 'aliases': ['青菜', '绿叶菜']},
    {'id': 'broccoli', 'name': '西兰花', 'slot': 'vegetable', 'amount': '搭配蔬菜', 'aliases': ['西兰花']},
]
for _food in FOODS:
    _food['image'] = '/static/food/cutouts/' + _food['id'] + '.webp'
    _food['group'] = _food['slot']
    if _food['id'] == 'broccoli':
        _food['image'] = '/static/food/broccoli.webp'
PLATES = [
    {'id': 'home-eggs', 'meal_types': ['lunch', 'dinner'], 'scenes': list(SCENES),
     'title': '番茄鸡蛋饭', 'foods': ['rice', 'tomato-egg', 'leafy-greens'],
     'method': '炒好番茄和鸡蛋，搭一碗米饭，再配一盘青菜。'},
    {'id': 'fish-rice', 'meal_types': ['lunch', 'dinner'], 'scenes': list(SCENES),
     'title': '米饭配鱼和青菜', 'foods': ['rice', 'fish', 'leafy-greens'],
     'method': '选清蒸或家常做法的鱼，主食和蔬菜一起搭。'},
    {'id': 'tofu-rice', 'meal_types': ['lunch', 'dinner'], 'scenes': list(SCENES),
     'title': '豆腐青菜饭', 'foods': ['rice', 'tofu', 'leafy-greens'],
     'method': '豆腐选自己喜欢的家常做法，再配米饭和青菜。'},
    {'id': 'morning-toast', 'meal_types': ['breakfast'], 'scenes': list(SCENES),
     'title': '面包、鸡蛋与蔬菜', 'foods': ['toast', 'boiled-eggs', 'leafy-greens'],
     'method': '面包和鸡蛋搭着吃，蔬菜可以选自己方便准备的。'},
    {'id': 'morning-potato', 'meal_types': ['breakfast'], 'scenes': list(SCENES),
     'title': '红薯配鸡蛋和青菜', 'foods': ['sweet-potato', 'boiled-eggs', 'leafy-greens'],
     'method': '蒸红薯搭配鸡蛋，再添一些蔬菜，按自己的食欲取用。'},
    {'id': 'chicken-rice', 'meal_types': ['lunch', 'dinner'], 'scenes': list(SCENES),
     'title': '鸡肉青菜饭', 'foods': ['rice', 'chicken', 'leafy-greens'],
     'method': '鸡肉、米饭和青菜分开选，外卖酱汁也可以分开放。'},
]


def options(meal_type='lunch', scene='home'):
    if meal_type not in MEALS or scene not in SCENES:
        raise APIError('VALIDATION_FAILED', '请选择有效的餐次和用餐场景', 422)
    return {'content_version': VERSION, 'meal_type': meal_type, 'scene': scene,
            'meals': MEALS, 'scenes': SCENES, 'slot_labels': SLOTS,
            'foods': deepcopy(FOODS),
            'plates': deepcopy([p for p in PLATES if meal_type in p['meal_types'] and scene in p['scenes']])}


def validate_suggestion(raw, avoid=()):
    if raw is None:
        return None
    if not isinstance(raw, dict) or set(raw) != {'content_version', 'meal_type', 'scene', 'slots'}:
        raise APIError('MEAL_SUGGESTION_INVALID', '餐食建议格式未通过核验', 422)
    if (not all(isinstance(raw[key],str) for key in ('content_version','meal_type','scene')) or
            raw['content_version'] != VERSION or raw['meal_type'] not in MEALS or raw['scene'] not in SCENES):
        raise APIError('MEAL_SUGGESTION_INVALID', '餐食建议已失效，请重新生成', 422)
    slots = raw['slots']
    if not isinstance(slots, dict) or set(slots) != set(SLOTS):
        raise APIError('MEAL_SUGGESTION_INVALID', '餐食建议需要主食、蛋白质和蔬菜', 422)
    by_id = {food['id']: food for food in FOODS}
    for slot, identifier in slots.items():
        if not isinstance(identifier, str) or identifier not in by_id or by_id[identifier]['slot'] != slot:
            raise APIError('MEAL_SUGGESTION_INVALID', '建议中的食物不在当前可用目录', 422)
        food = by_id[identifier]
        if any(name and any(name in alias or alias in name for alias in food['aliases'] + [food['name']])
               for name in avoid):
            raise APIError('MEAL_SUGGESTION_RESTRICTED', '建议包含已明确不吃的食物，请重新选择', 422)
    return deepcopy(raw)


SUGGESTION_PROMPT = (
    '\n本任务如提出一份可采用的餐食，可以在最终JSON额外增加meal_suggestion；不适合时用null或省略。'
    '该对象只允许{content_version:"' + VERSION +
    '",meal_type:"breakfast或lunch或dinner",scene:"home或canteen或takeaway",'
    'slots:{staple:"rice或toast或sweet-potato",protein:"tomato-egg或tofu或fish或chicken或boiled-eggs",'
    'vegetable:"leafy-greens"}}。只选本次建议真正提到且符合本人明确忌口的食物；不可声称营养等价或推算克数。'
    '这是计划吃的建议，不能表述为已吃过，也不自动保存餐食。'
)
