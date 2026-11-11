"""14-E：按姿态提取前的画面核查截取纯开合跳，不把混合原片当单动作输入。"""
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,project_path,configure_environment
configure_environment()
import av


def prepare():
    manifest=ROOT/'data/datasets/development-movements/sources.json';doc=json.loads(manifest.read_text(encoding='utf-8'))
    source=next(s for s in doc['sources'] if s['id']=='commons-jumping-jacks');path=project_path(source['path'])
    if hashlib.sha256(path.read_bytes()).hexdigest()!=source['sha256']:raise ValueError('混合原片身份改变')
    target=project_path(path.with_name('jumping-jacks-commons-segment.mp4'));key='commons-jumping-jacks-segment'
    prior=next((s for s in doc['sources'] if s['id']==key),None)
    if target.exists():
        if not prior or hashlib.sha256(target.read_bytes()).hexdigest()!=prior['sha256']:raise ValueError('既有截取输入身份不匹配，不覆盖')
        print(json.dumps({'cache_hit':True,'path':str(target)}));return
    if prior:raise ValueError('已登记截取输入缺失，先核查')
    count=0
    with av.open(str(path)) as original,av.open(str(target),'w') as output:
        stream=original.streams.video[0];encoded=output.add_stream('libx264',rate=stream.average_rate)
        encoded.width=stream.width;encoded.height=stream.height;encoded.pix_fmt='yuv420p';encoded.options={'crf':'18','preset':'medium'}
        for frame in original.decode(stream):
            if frame.time>=2.9:break
            for packet in encoded.encode(frame):output.mux(packet)
            count+=1
        for packet in encoded.encode():output.mux(packet)
    source.update(role='mixed_action_source',variant_review='Codex frame review: frontal jumping jacks first, then burpees, later side-view combinations; mixed video not used as a single-action positive.')
    derivative={**source,'id':key,'path':target.relative_to(ROOT).as_posix(),'bytes':target.stat().st_size,'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),
        'role':'development_candidate','variant_review':'Codex frame review before pose extraction: initial [0, 2.9) seconds, one frontal person, hands and feet visible; no burpee portion, no spatial crop or repeated padding.',
        'derived_from':'commons-jumping-jacks','transformation':{'time_range_seconds':[0,2.9],'crop':None,'timestamps':'source timestamps preserved','video':'H.264 yuv420p CRF18 medium','audio':'omitted','script':'scripts/prepare_jumping_jack_input.py'}}
    doc['sources'].append(derivative);manifest.write_text(json.dumps(doc,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'cache_hit':False,'frames':count,'bytes':derivative['bytes'],'path':str(target)},ensure_ascii=False))


if __name__=='__main__':prepare()
