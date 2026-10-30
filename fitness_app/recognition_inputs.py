"""已核动作类型的公开扩展来源；同源去重，不提供具体错误真值。"""
import hashlib
import json
import numpy as np
from .paths import ROOT,project_path
from .automatic_recognition import prepare_frame
from .frame_pipeline import usable_point
from .sessions import IdentityContinuity

INPUTS=(
    ('dvids-551833','data/verification/step-14/dvids-551833-poses.npz','public_dvids_collection','train'),
    ('dvids-640237-side','data/verification/step-14/dvids-640237-side-poses.npz','public_dvids_collection','train'),
    ('commons-jumping-jacks-segment','data/verification/step-14/commons-jumping-jacks-segment-poses.npz','commons_jacks_original','train'),
    ('dvids-639914-side','data/verification/step-14/dvids-639914-side-poses.npz','public_dvids_collection','train'),
    ('dvids-692514-front','data/verification/step-19/military-press-front-poses.npz','public_dvids_collection','train'),
    ('commons-fitnessscape-row','data/verification/step-19/fitnessscape-row-poses.npz','fitnessscape_0I6q9NqK9tM','validation'),
    ('commons-overhead-press','data/verification/step-19/fitnessscape-press-poses.npz','fitnessscape_0I6q9NqK9tM','validation'),
)


def extra_inputs():
    manifest=json.loads((ROOT/'data/datasets/development-movements/sources.json').read_text(encoding='utf-8'))
    sources={s['id']:s for s in manifest['sources']};entries=[];seen=set()
    for source_id,cache,group,partition in INPUTS:
        source=sources[source_id];path=project_path(source['path']);cache=project_path(cache)
        digest=hashlib.sha256(path.read_bytes()).hexdigest()
        if digest!=source['sha256']:raise ValueError('公开动作来源摘要改变')
        if digest in seen:raise ValueError('同源动作样本重复')
        seen.add(digest)
        with np.load(cache,allow_pickle=False) as archive:meta=json.loads(str(archive['metadata']))
        if meta.get('source_sha256')!=digest:raise ValueError('公开缓存不对应所选原片')
        entries.append({'key':'public:'+digest,'action':source['action_id'],'source_id':source_id,'source':source['path'],
            'source_sha256':digest,'cache':cache.relative_to(ROOT).as_posix(),'cache_sha256':hashlib.sha256(cache.read_bytes()).hexdigest(),
            'group':group,'partition':partition})
    if {e['group'] for e in entries if e['partition']=='train'} & {e['group'] for e in entries if e['partition']=='validation'}:
        raise ValueError('公开动作拟合／验证来源组交叉')
    return entries


def extra_frames(entry):
    with np.load(project_path(entry['cache']),allow_pickle=False) as archive:
        continuity=IdentityContinuity();frames=[]
        for i,stamp in enumerate(archive['timestamps_ms']):
            points=[dict(zip(('x','y','z','visibility','presence'),map(float,p))) for p in archive['points'][i]]
            for p in points:p['valid']=usable_point(p)
            frame={'captured_ms':int(stamp),'frame_seq':i,'width':int(archive['sizes'][i,0]),'height':int(archive['sizes'][i,1]),
                'framing':{'person_count':int(archive['person_counts'][i]),'landmarks':points}}
            frame['identity_accepted']=bool(archive['identity_accepted'][i]) if 'identity_accepted' in archive.files else continuity.accept(frame)
            frames.append(prepare_frame(frame))
        return frames
