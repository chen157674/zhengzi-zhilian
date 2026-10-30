"""展示素材独立于算法能力快照；图片变化不改写历史检测能力身份。"""
from functools import lru_cache
import hashlib
import json
import re

from .paths import ROOT,project_path

MANIFEST=ROOT/'data/presentation-assets.json'
DISPLAY_ANNOTATION_VERSION=1


@lru_cache(maxsize=4)
def _read(modified,size):
    data=json.loads(MANIFEST.read_text(encoding='utf-8'))
    if data.get('schema_version')!=1 or not isinstance(data.get('covers'),dict):raise ValueError('展示资源清单无效')
    return data


@lru_cache(maxsize=48)
def _valid(path,modified,size,expected_size,sha256):
    return size==expected_size and hashlib.sha256(project_path(path).read_bytes()).hexdigest()==sha256


def cover_catalogue():
    try:
        stat=MANIFEST.stat();sources=_read(stat.st_mtime_ns,stat.st_size)['covers']
    except (OSError,ValueError,TypeError):return {}
    available={}
    for action,item in sources.items():
        try:
            if not isinstance(action,str) or not re.fullmatch(r'[a-z_]+',action) or not isinstance(item,dict):continue
            path=project_path(item['path']);expected=ROOT/'fitness_app/static/covers'/f'{action}.png'
            if path!=expected.resolve():continue
            sha=item['sha256']
            if not isinstance(sha,str) or not re.fullmatch(r'[a-f0-9]{64}',sha):continue
            stat=path.stat()
            if not _valid(str(path),stat.st_mtime_ns,stat.st_size,item['bytes'],sha):continue
            available[action]={'url':f'/static/covers/{action}.png?v={sha[:12]}','width':item['width'],'height':item['height']}
        except (OSError,ValueError,KeyError,TypeError):continue
    return available


def demo_resource(demo_id=None,require_track=True):
    """只发布清单登记的演示，不接受请求中指定的磁盘路径。"""
    try:
        stat=MANIFEST.stat();doc=_read(stat.st_mtime_ns,stat.st_size)
        if demo_id is None:demo_id=doc.get('home_demo_id','barbell-row')
        if not isinstance(demo_id,str) or not re.fullmatch(r'[a-z0-9-]{1,64}',demo_id):return None
        item=doc.get('demos',{}).get(demo_id)
        if not isinstance(item,dict) or item.get('id')!=demo_id:return None
        from .catalogue import get_action
        if not get_action(item.get('action_id')):return None
        checks=[('video','.mp4')]
        if require_track:checks.append(('track','.json'))
        if item.get('poster'):checks.append(('poster','.jpg'))
        for kind,suffix in checks:
            asset=item[kind];path=project_path(asset['path'])
            directories=('data/datasets/development-movements','data/datasets/user-demos') if kind=='video' else ('fitness_app/static/demo',)
            if not any(path.is_relative_to((ROOT/directory).resolve()) for directory in directories):return None
            if path.suffix!=suffix or not isinstance(asset['sha256'],str) or not re.fullmatch(r'[a-f0-9]{64}',asset['sha256']):return None
            s=path.stat()
            if not _valid(str(path),s.st_mtime_ns,s.st_size,asset['bytes'],asset['sha256']):return None
        annotation=item.get('display_annotation')
        track_version=hashlib.sha256((item['track']['sha256']+str(DISPLAY_ANNOTATION_VERSION)+json.dumps(annotation,ensure_ascii=False,sort_keys=True)).encode()).hexdigest() if annotation and item.get('track') else item.get('track',{}).get('sha256','')
        return {**item,'track_etag':track_version,'video_url':f"/static/demo/{demo_id}/video?v={item['video']['sha256'][:12]}",
            'track_url':f"/static/demo/{demo_id}/track?v={track_version[:12]}" if item.get('track') else None,
            'poster_url':f"/static/demo/{demo_id}/poster?v={item['poster']['sha256'][:12]}" if item.get('poster') else None}
    except (OSError,ValueError,KeyError,TypeError):return None


def demo_catalogue():
    try:
        stat=MANIFEST.stat();doc=_read(stat.st_mtime_ns,stat.st_size)
        ids=doc.get('active_demo_ids',[doc.get('home_demo_id','barbell-row')])
        if not isinstance(ids,list) or len(ids)>20:return []
        return [item for key in dict.fromkeys(ids) if (item:=demo_resource(key,require_track=False))]
    except (OSError,ValueError,KeyError,TypeError):return []


def home_demo_catalogue():
    """首页仅轮播带有已校验同源轨迹的白名单演示。"""
    try:
        stat=MANIFEST.stat();doc=_read(stat.st_mtime_ns,stat.st_size)
        ids=doc.get('home_demo_ids',[doc.get('home_demo_id','barbell-row')])
        if not isinstance(ids,list) or not 1<=len(ids)<=8:return []
        return [item for key in dict.fromkeys(ids) if (item:=demo_resource(key))]
    except (OSError,ValueError,KeyError,TypeError):return []


def demo_display_track(demo):
    """构造演示标注副本，磁盘轨迹和真实分析结果保持原始含义。"""
    annotation=demo.get('display_annotation')
    if not annotation:return None
    if annotation.get('source')!='user_confirmed_demo_annotation' or annotation.get('scope')!='whole_video':
        raise ValueError('演示标注来源或范围无效')
    issues=annotation['issues'];instruction=annotation['instruction']
    if not isinstance(issues,list) or not issues or not all(isinstance(x,str) and x.strip() for x in issues) or not isinstance(instruction,str) or not instruction.strip():
        raise ValueError('演示标注内容无效')
    track=json.loads(project_path(demo['track']['path']).read_text(encoding='utf-8'))
    track['display_annotation']=annotation
    track['model_results_unchanged']=True
    for frame in track['frames']:
        frame['model_feedback']=frame['feedback']
        points=frame['p']
        joints=[i for i in (11,12,23,24) if points[i] is not None] if frame.get('people')==1 else []
        frame['feedback']={
            'schema_version':1,'state':'issue','title':'、'.join(issues),
            'description':'用户已确认的全片演示问题。','instruction':instruction,
            'scope_note':'演示标注；模型检测结果单独保留。',
            'display_basis':annotation['source'],'overall_correctness':None,
            'checks':[{'code':'demo.annotation.'+str(i),'label':label,'status':'issue',
                       'detail':'用户确认的演示标注','reason':None,'side':'none'} for i,label in enumerate(issues)],
            'highlight':{'joint_indices':joints,'meaning':'user_confirmed_demo_region'} if joints else None}
    return track

