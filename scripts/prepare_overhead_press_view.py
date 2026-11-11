"""14-G 固定提取军姿推举原片右侧机位，不把两个不同步画面混成一个人。"""
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,project_path,configure_environment
configure_environment()
import av
import numpy as np


def prepare(view='side'):
    if view not in {'side','front'}:raise ValueError('只支持已核固定面板')
    manifest=ROOT/'data/datasets/development-movements/sources.json';doc=json.loads(manifest.read_text(encoding='utf-8'))
    source=next(s for s in doc['sources'] if s['id']=='dvids-692514');path=project_path(source['path'])
    if hashlib.sha256(path.read_bytes()).hexdigest()!=source['sha256']:raise ValueError('推举原片身份改变')
    target=project_path(path.with_name(f'overhead-press-dvids-692514-{view}.mp4'));key=f'dvids-692514-{view}'
    prior=next((s for s in doc['sources'] if s['id']==key),None)
    if target.exists():
        if not prior or hashlib.sha256(target.read_bytes()).hexdigest()!=prior['sha256']:raise ValueError('既有侧面输入身份不匹配，不覆盖')
        print(json.dumps({'cache_hit':True,'path':str(target)}));return
    if prior:raise ValueError('已登记侧面输入缺失，先核查')
    crop={'x':316 if view=='side' else 0,'y':0,'width':324 if view=='side' else 312,'height':480};count=0
    with av.open(str(path)) as original,av.open(str(target),'w') as output:
        stream=original.streams.video[0]
        if (stream.width,stream.height)!=(640,480):raise ValueError('预先核查的画面尺寸改变')
        encoded=output.add_stream('libx264',rate=stream.average_rate);encoded.width=crop['width'];encoded.height=480;encoded.pix_fmt='yuv420p';encoded.options={'crf':'18','preset':'medium'}
        for frame in original.decode(stream):
            image=frame.to_ndarray(format='rgb24')[:,crop['x']:crop['x']+crop['width']]
            selected=av.VideoFrame.from_ndarray(np.ascontiguousarray(image),format='rgb24');selected.pts=frame.pts;selected.time_base=frame.time_base
            for packet in encoded.encode(selected):output.mux(packet)
            count+=1
        for packet in encoded.encode():output.mux(packet)
    source.update(role='multiview_source',variant_review='Codex frame review: standing two-hand bar press in unsynchronized frontal and side views; native-size review shows lower legs clipped in original. No inference about bar mass or material.')
    derivative={**source,'id':key,'path':target.relative_to(ROOT).as_posix(),'bytes':target.stat().st_size,'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),
        'role':'development_counterexample','variant_review':f'Fixed {view} panel retained; original lower legs/feet clipped. Upper-body posture reference only; cannot verify full standing variant or repetitions.',
        'derived_from':'dvids-692514','transformation':{'crop_pixels':crop,'time_range':'entire original','timestamps':'source timestamps preserved','video':'H.264 yuv420p CRF18 medium','audio':'omitted','script':'scripts/prepare_overhead_press_view.py'}}
    doc['sources'].append(derivative);manifest.write_text(json.dumps(doc,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'cache_hit':False,'frames':count,'bytes':derivative['bytes'],'path':str(target)},ensure_ascii=False))


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--view',choices=['side','front'],default='side')
    prepare(parser.parse_args().view)
