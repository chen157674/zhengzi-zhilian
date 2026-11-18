"""入门动作教学：产品手工整理，出处沿已发布知识条目核对；不生成用户训练事实。"""
from copy import deepcopy
from functools import lru_cache

GUIDES = {
    'bodyweight_squat': {
        'category':'徒手', 'focus':'腿部 · 臀部', 'level':'基础入门',
        'tagline':'先学会稳定下蹲，再慢慢增加难度。',
        'image':'/static/learn/bodyweight-squat.webp',
        'image_alt':'中国成年女性演示徒手深蹲的站立、下蹲和站起三个阶段',
        'phases':['站稳准备','屈髋下蹲','控制站起'],
        'steps':[
            {'title':'先站稳','text':'双脚约与肩同宽，脚尖自然微向外，双脚踩稳地面。'},
            {'title':'臀部向后坐','text':'屈髋屈膝，像坐向身后的椅子；膝盖朝脚尖方向移动。'},
            {'title':'下到可控的位置','text':'保持脚跟踩稳，蹲到自己能控制的深度，再平稳站起。'}],
        'reminders':['脚跟不要抬起','膝盖顺着脚尖方向','不追求越低越好'],
        'first_try':'先空手慢慢试做，找到双脚稳定、下蹲可控的感觉。',
        'citation_titles':['徒手深蹲起始','深蹲下行控制','深蹲上行控制'],
    },
    'standing_dumbbell_curl': {
        'category':'哑铃', 'focus':'手臂', 'level':'基础入门',
        'tagline':'上臂稳住，抬起和放下都保持控制。',
        'image':'/static/learn/dumbbell-curl.webp',
        'image_alt':'中国成年男性演示站姿哑铃弯举的准备、屈肘抬起和控制下放三个阶段',
        'phases':['站稳握铃','屈肘抬起','缓慢下放'],
        'steps':[
            {'title':'轻握哑铃站稳','text':'选择能轻松控制的哑铃，双脚站稳，手腕与前臂保持自然对齐。'},
            {'title':'屈肘抬起','text':'保持上臂靠近躯干，缓慢弯曲肘部抬起哑铃，身体不要前后甩动。'},
            {'title':'控制放下','text':'主动控制哑铃回到起点，不让重量带着手臂快速掉落。'}],
        'reminders':['不靠身体摆动甩起','手腕保持自然','放下也要慢'],
        'first_try':'从能稳定握持的轻重量开始，先把抬起和下放做顺。',
        'citation_titles':['弯举手腕','弯举抬起控制','弯举下放控制'],
    },
    'machine_chest_press': {
        'category':'器械', 'focus':'胸部 · 手臂', 'level':'基础入门',
        'tagline':'先调好座椅，再平稳推起和回收。',
        'image':'/static/learn/machine-chest-press.webp',
        'image_alt':'中国成年女性演示坐姿器械推胸的坐稳握把、向前推起和控制回程三个阶段',
        'phases':['调座椅握把','平稳前推','控制回程'],
        'steps':[
            {'title':'先调座椅','text':'按器械说明调节座椅，让把手在胸部附近；背部贴稳靠背，双脚踩稳。'},
            {'title':'握住并向前推','text':'完整握住把手，手腕与前臂自然对齐，平稳向前推，不突然冲出去。'},
            {'title':'缓慢收回','text':'保持背部支撑，主动控制把手回到起点，不让配重快速回弹。'}],
        'reminders':['先调座椅和起始位置','背部贴稳靠背','回程不要砸配重'],
        'first_try':'先用轻配重熟悉器械，确认能平稳推起并控制回程。',
        'citation_titles':['座椅与把手高度','脚部支撑','背部支撑','推胸手腕方向','推胸回程'],
    },
}


# 新增动作教学内容与生成任务共用一个清单，图片未通过审查时不发布卡片。
from pathlib import Path
import json
CURRICULUM_PATH=Path(__file__).with_name('learning_curriculum.json')
CURRICULUM=json.loads(CURRICULUM_PATH.read_text(encoding='utf-8'))['guides']
for _action_id,_lesson in CURRICULUM.items():
    GUIDES[_action_id]={key:value for key,value in _lesson.items() if key not in {'image_prompt_movement','editorial_source','status'}}
    GUIDES[_action_id]['citation_ids']=['k:lesson:'+_action_id]


@lru_cache(maxsize=4)
def _reviewed_images(path, modified, size):
    return {row['action_id']:row for row in json.loads(Path(path).read_text(encoding='utf-8'))['images']}


@lru_cache(maxsize=96)
def _verified_image(path, modified, changed, size, expected_size, expected_hash):
    import hashlib
    return size==expected_size and hashlib.sha256(Path(path).read_bytes()).hexdigest()==expected_hash


@lru_cache(maxsize=8)
def _material_manifest(path,modified,size):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def training_picture(action_id):
    names={'bodyweight_squat':'bodyweight-squat-scene',
           'standing_dumbbell_curl':'dumbbell-curl-scene',
           'forearm_plank':'forearm-plank-scene',
           'machine_chest_press':'machine-chest-press-scene'}
    identifier=names.get(action_id)
    if not identifier:return None
    folder=Path(__file__).with_name('static')/'training';manifest=folder/'sources.json'
    if not manifest.is_file():return None
    stamp=manifest.stat()
    rows=_material_manifest(str(manifest),stamp.st_mtime_ns,stamp.st_size)['new_assets']
    item=next((x for x in rows if x['id']==identifier),None)
    if not item or item.get('review_status')!='compressed_contact_sheet_reviewed_not_expert_certified':return None
    web=item['web_asset'];path=folder/Path(web['file']).name
    if not path.is_file():return None
    stamp=path.stat()
    if not _verified_image(str(path),stamp.st_mtime_ns,stamp.st_ctime_ns,stamp.st_size,web['bytes'],web['sha256']):return None
    return {'url':'/static/training/'+path.name,'alt':item['label']+'，动作示意','width':web['size'][0],'height':web['size'][1]}


def teaching_views(action_id):
    folder=Path(__file__).with_name('static')/'learn/cutouts';manifest=folder/'sources.json'
    if not manifest.is_file():return []
    stamp=manifest.stat();data=_material_manifest(str(manifest),stamp.st_mtime_ns,stamp.st_size)
    definitions=data.get('views',{}).get(action_id,{})
    rows={row['id']:row for row in data['images']};result=[]
    for identifier,ids in definitions.items():
        images=[]
        for key in ids:
            item=rows.get(key)
            if not item or item.get('review_status') not in {'visual_layout_reviewed_not_expert_certified','compressed_contact_sheet_reviewed_not_expert_certified'}:break
            web=item['web_asset'];path=folder/Path(web['file']).name
            if not path.is_file():break
            stamp=path.stat()
            if not _verified_image(str(path),stamp.st_mtime_ns,stamp.st_ctime_ns,stamp.st_size,web['bytes'],web['sha256']):break
            images.append({'url':'/static/learn/cutouts/'+path.name,'alt':item['label'],'width':web['size'][0],'height':web['size'][1]})
        if len(images)==3:
            result.append({'id':identifier,'label':{'front':'正面','side':'侧面'}[identifier],'images':images})
    return result


def tutorial_for(store, action_id):
    """教学步骤仅在支持条目仍发布时可用；撤回不能继续支持旧教程。"""
    with store.lock:
        template=GUIDES.get(action_id)
        if not template:return None
        if action_id in CURRICULUM and CURRICULUM[action_id].get('status')!='reviewed':return None
        rows=[r for r in store.by_id.values() if r.get('status')=='published' and r.get('action_id')==action_id and r.get('source_type')!='project_rule']
        if 'citation_ids' in template:
            by_id={r['id']:r for r in rows}
            if not all(identifier in by_id for identifier in template['citation_ids']):return None
            selected=[by_id[identifier] for identifier in template['citation_ids']]
        else:
            by_title={r['title']:r for r in rows}
            if not all(title in by_title for title in template['citation_titles']):return None
            selected=[by_title[title] for title in template['citation_titles']]
        value=deepcopy(template);value.pop('citation_titles',None);value.pop('citation_ids',None)
        value['source_ids']=[row['id'] for row in selected]
        value['sources']=[store.public_row(row) for row in selected]
        # 发布内容前验证审查时锁定的网页图片身份。
        from .paths import project_path
        manifest=Path(__file__).with_name('static').joinpath('learn/sources.json')
        stamp=manifest.stat()
        asset=_reviewed_images(str(manifest),stamp.st_mtime_ns,stamp.st_size).get(action_id)
        if not asset or asset.get('review_status')!='reviewed':return None
        path=project_path('fitness_app'+value['image']);web=asset['web_asset']
        if path.name!=web['file'] or not path.is_file():return None
        stamp=path.stat()
        if not _verified_image(str(path),stamp.st_mtime_ns,stamp.st_ctime_ns,stamp.st_size,web['bytes'],web['sha256']):return None
        value['views']=teaching_views(action_id)
        value['thumbnail']=training_picture(action_id)
        return value


def learning_cards(store, actions=None):
    from .catalogue import definitions
    actions={action['action_id']:action for action in (definitions() if actions is None else actions)}
    cards=[]
    for action_id in GUIDES:
        lesson=tutorial_for(store,action_id)
        if lesson:
            action=actions.get(action_id)
            if not action:continue
            previews=next((view['images'][:2] for view in lesson.get('views',[]) if view['id']=='side'),[])
            cards.append({'action_id':action_id,'name':action['name'],**{key:lesson[key] for key in ['category','focus','level','tagline','image','image_alt']},'thumbnail':lesson.get('thumbnail'),'preview_frames':previews,'url':'/actions/'+action_id})
    return cards


def related_tutorial(store,matches):
    # Match an actual retrieved action, never force an unrelated exercise on nutrition queries.
    for row in matches:
        if row.get('domain')=='movement':
            lesson=tutorial_for(store,row.get('action_id'))
            if lesson:
                from .catalogue import get_action
                return {'action_id':row['action_id'],'name':get_action(row['action_id'])['name'],**lesson}
    return None
