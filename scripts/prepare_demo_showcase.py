"""18-B 从已核公开开发姿态导出同帧展示轨迹；不重新推理或生成演示次数。"""
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,project_path,configure_environment
configure_environment()
from diagnose_barbell_row import prepare_frames
from fitness_app.database import json_text


def prepare():
    manifest=ROOT/'data/presentation-assets.json';doc=json.loads(manifest.read_text(encoding='utf-8'))
    sources_path=ROOT/'data/datasets/development-movements/sources.json';sources=json.loads(sources_path.read_text(encoding='utf-8'))
    source=next(s for s in sources['sources'] if s['id']=='dvids-639914-side');video=project_path(source['path'])
    evidence=ROOT/'data/verification/step-14/barbell-row-development.json';prior=json.loads(evidence.read_text(encoding='utf-8'))
    cache=ROOT/'data/verification/step-14/dvids-639914-side-poses.npz'
    identity={'source_sha256':source['sha256'],'pose_cache_sha256':hashlib.sha256(cache.read_bytes()).hexdigest(),
        'development_evidence_sha256':hashlib.sha256(evidence.read_bytes()).hexdigest(),'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    path=project_path('fitness_app/static/demo/barbell-row-track.json');old=doc.get('demos',{}).get('barbell-row')
    if old and old.get('build_identity')==identity and path.exists() and hashlib.sha256(path.read_bytes()).hexdigest()==old['track']['sha256']:
        print(json.dumps({'cache_hit':True,'frames':old['frames'],'new_pose_inference':False}));return
    if video.stat().st_size!=source['bytes'] or hashlib.sha256(video.read_bytes()).hexdigest()!=source['sha256']:raise ValueError('演示源身份不符')
    frames,_,hit=prepare_frames()
    if not hit:raise ValueError('现有公开姿态的图像范围证据应复用')
    timeline=[]
    for frame in frames:
        q=frame['framing'];visible=q['usable'] and frame['identity_accepted']
        points=[[round(p['x'],6),round(p['y'],6)] if p.get('valid') else None for p in q['landmarks']] if visible else [None]*33
        timeline.append({'t':frame['captured_ms'],'p':points})
    track={'schema_version':1,'source_sha256':source['sha256'],'width':frames[0]['width'],'height':frames[0]['height'],
        'mode':'preprocessed_public_source_not_live','frames':timeline,'events':[{'start_ms':e['start_ms'],'end_ms':e['end_ms']} for e in prior['events']],
        'accuracy_status':'no_independent_ground_truth','build_identity':identity}
    path.parent.mkdir(exist_ok=True);path.write_text(json_text(track)+'\n',encoding='utf-8')
    doc.setdefault('demos',{})['barbell-row']={'id':'barbell-row','action_id':'barbell_bent_over_row','title':'杠铃俯身划船','frames':len(frames),'width':516,'height':290,
        'video':{'path':source['path'],'bytes':source['bytes'],'sha256':source['sha256']},
        'track':{'path':path.relative_to(ROOT).as_posix(),'bytes':path.stat().st_size,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()},
        'source_url':source['source_url'],'credit':source['author'],'license':'PUBLIC DOMAIN，遵守DVIDS使用限制',
        'terms_url':source['terms_url'],'non_endorsement':'The appearance of U.S. Department of War (DoW) visual information does not imply or constitute DoW endorsement.',
        'use_scope':'Private local app effect preview and analysis demo; no tutorial, advertising, implied endorsement or public redistribution',
        'build_identity':identity}
    manifest.write_text(json.dumps(doc,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    source['use_scope']=doc['demos']['barbell-row']['use_scope'];source['app_demo_id']='barbell-row'
    sources_path.write_text(json.dumps(sources,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'cache_hit':False,'frames':len(frames),'track_bytes':path.stat().st_size,'reference_events':len(prior['events']),'new_pose_inference':False,'duplicated_video':False}))


if __name__=='__main__':prepare()
