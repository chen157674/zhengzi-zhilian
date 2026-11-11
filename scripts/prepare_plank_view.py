"""14-D：从已核公有领域双画面原片固定提取完整侧面面板，不跟随人体裁剪。"""
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
    source=next(s for s in doc['sources'] if s['id']=='dvids-640237');path=project_path(source['path'])
    if hashlib.sha256(path.read_bytes()).hexdigest()!=source['sha256']:raise ValueError('分屏原片身份改变')
    target=project_path(path.with_name('plank-dvids-640237-side.mp4'));key='dvids-640237-side'
    prior=next((s for s in doc['sources'] if s['id']==key),None)
    if target.exists():
        if not prior or hashlib.sha256(target.read_bytes()).hexdigest()!=prior['sha256']:raise ValueError('既有侧面输入身份不匹配，不能覆盖')
        print(json.dumps({'cache_hit':True,'path':str(target)}));return
    if prior:raise ValueError('已登记侧面输入缺失，先核查而非自动覆盖身份')
    # 面板边界先根据原图核对；保留整个侧面画面及全部源时间，不按结果挑片段。
    crop={'x':266,'y':288,'width':524,'height':288};count=0
    with av.open(str(path)) as original,av.open(str(target),'w') as output:
        stream=original.streams.video[0];encoded=output.add_stream('libx264',rate=stream.average_rate)
        encoded.width=crop['width'];encoded.height=crop['height'];encoded.pix_fmt='yuv420p';encoded.options={'crf':'18','preset':'medium'}
        for frame in original.decode(stream):
            image=frame.to_ndarray(format='rgb24')[288:576,266:790]
            selected=av.VideoFrame.from_ndarray(np.ascontiguousarray(image),format='rgb24');selected.pts=frame.pts;selected.time_base=frame.time_base
            for packet in encoded.encode(selected):output.mux(packet)
            count+=1
        for packet in encoded.encode():output.mux(packet)
    source.update(role='multiview_source',variant_review='Codex frame review: same person in stacked frontal/side panels; original composite is not a single-person input.')
    derivative={**source,'id':key,'path':target.relative_to(ROOT).as_posix(),'bytes':target.stat().st_size,'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),
        'role':'development_candidate','variant_review':'Codex frame review: fixed full lower side panel shows forearm plank entry, hold and exit; single person, body and supporting arms/feet visible.',
        'derived_from':'dvids-640237','transformation':{'crop_pixels':crop,'time_range':'entire original','timestamps':'source timestamps preserved',
            'video':'H.264 yuv420p CRF18 medium','audio':'omitted','script':'scripts/prepare_plank_view.py'},
        'ground_truth':'No independently annotated hold intervals or quality labels. The entire clip duration is not hold-time truth.'}
    doc['sources'].append(derivative);manifest.write_text(json.dumps(doc,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'cache_hit':False,'frames':count,'bytes':derivative['bytes'],'path':str(target)},ensure_ascii=False))


if __name__=='__main__':prepare()
