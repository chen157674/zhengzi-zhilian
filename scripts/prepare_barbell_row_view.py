"""14-H 固定提取完整上方斜侧面板，保留准备、划船和站起结束全部源时间。"""
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,project_path,configure_environment
configure_environment()
import av
import numpy as np


def prepare():
    manifest=ROOT/'data/datasets/development-movements/sources.json';doc=json.loads(manifest.read_text(encoding='utf-8'))
    source=next(s for s in doc['sources'] if s['id']=='dvids-639914');path=project_path(source['path'])
    if hashlib.sha256(path.read_bytes()).hexdigest()!=source['sha256']:raise ValueError('划船原片身份改变')
    target=project_path(path.with_name('barbell-row-dvids-639914-side.mp4'));key='dvids-639914-side'
    prior=next((s for s in doc['sources'] if s['id']==key),None)
    if target.exists():
        if not prior or hashlib.sha256(target.read_bytes()).hexdigest()!=prior['sha256']:raise ValueError('既有输入身份不匹配，不覆盖')
        print(json.dumps({'cache_hit':True,'path':str(target)}));return
    if prior:raise ValueError('已登记输入缺失，先核查')
    # 先以原尺寸图确定固定面板，不依据计数输出挑时间或跟随人裁切。
    crop={'x':270,'y':4,'width':516,'height':290};count=0
    with av.open(str(path)) as original,av.open(str(target),'w') as output:
        stream=original.streams.video[0]
        if (stream.width,stream.height)!=(1024,576):raise ValueError('已核画面尺寸改变')
        encoded=output.add_stream('libx264',rate=stream.average_rate);encoded.width=516;encoded.height=290
        encoded.pix_fmt='yuv420p';encoded.options={'crf':'18','preset':'medium'}
        for frame in original.decode(stream):
            selected=av.VideoFrame.from_ndarray(np.ascontiguousarray(frame.to_ndarray(format='rgb24')[4:294,270:786]),format='rgb24')
            selected.pts=frame.pts;selected.time_base=frame.time_base
            for packet in encoded.encode(selected):output.mux(packet)
            count+=1
        for packet in encoded.encode():output.mux(packet)
    derivative={**source,'id':key,'path':target.relative_to(ROOT).as_posix(),'bytes':target.stat().st_size,'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),
        'role':'development_candidate','variant_review':'Fixed complete upper diagonal-side panel; single person, full body/feet, bar pickup, bent-over rows and standing exit. Wrist/bar occlusion must still be checked frame by frame.',
        'derived_from':source['id'],'transformation':{'crop_pixels':crop,'time_range':'entire original','timestamps':'source timestamps preserved','video':'H.264 yuv420p CRF18 medium','audio':'omitted','script':'scripts/prepare_barbell_row_view.py'}}
    doc['sources'].append(derivative);manifest.write_text(json.dumps(doc,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'cache_hit':False,'frames':count,'bytes':derivative['bytes'],'path':str(target)},ensure_ascii=False))


if __name__=='__main__':prepare()
