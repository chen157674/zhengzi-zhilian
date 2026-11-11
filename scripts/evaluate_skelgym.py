"""固定MIT时序模型的离线对照；独立于产品开关，不把上游成绩复制为本地验收。"""
import sys,json,hashlib,time,importlib.util,argparse
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment,project_path
configure_environment()
import numpy as np
import torch
from fitness_app.pose import PoseService
from fitness_app.content_bounds import detect_content_bounds

MODEL_DIR=ROOT/'data/models/skelgym'
OUT=ROOT/'data/verification/step-22'
JOINTS=[0,11,12,13,14,15,16,23,24,25,26,27,28]
CASES=[('commons-bench-fitnessscape','bench press',0,7100),('commons-pullups-fitnessscape','pull Up',0,6000),
       ('commons-cdc-chest-press','chest press unsupported',18000,30000)]


def model():
    manifest=json.loads((MODEL_DIR/'manifest.json').read_text(encoding='utf-8'))
    for item in manifest['files']:
        path=project_path(item['path'])
        if hashlib.sha256(path.read_bytes()).hexdigest()!=item['sha256']:raise ValueError('上游模型文件摘要变化')
    checkpoint=torch.load(MODEL_DIR/'transformer-world.pt',map_location='cpu',weights_only=True)
    state=checkpoint['model_state_dict']
    spec=importlib.util.spec_from_file_location('skelgym_reference_transformer',ROOT/'third_party/Gym_Classification/src/models/transformer.py')
    source=importlib.util.module_from_spec(spec);spec.loader.exec_module(source)
    net=source.TransformerModel(feat_dim=39,num_classes=22,d_model=112,nhead=8,num_layers=3,dim_feedforward=168)
    net.load_state_dict(state,strict=True);net.eval();torch.set_num_threads(2)
    with np.load(MODEL_DIR/'normalization-world.npz',allow_pickle=False) as data:mean=data['mean'].copy();std=data['std'].copy()
    if mean.shape[-1]!=39 or std.shape!=mean.shape or not np.isfinite(mean).all() or not np.isfinite(std).all():raise ValueError('归一化契约不一致')
    labels=json.loads((ROOT/'third_party/Gym_Classification/artifacts/reference/class_names.json').read_text())
    return net,labels,mean,np.maximum(std,1e-6)


def extract(case,entry,heavy):
    import av
    key,label,start,end=case;path=project_path(entry['path']);suffix='world' if 'heavy' in heavy.name else 'full-world'
    target=OUT/f'{key}-{start}-{end}-{suffix}.npz'
    identity={'source_sha256':entry['sha256'],'model_sha256':hashlib.sha256(heavy.read_bytes()).hexdigest(),
              'start_ms':start,'end_ms':end,'policy':'heavy-content-world-1' if 'heavy' in heavy.name else 'full-content-world-1','sampling':'native_fps'}
    if hashlib.sha256(path.read_bytes()).hexdigest()!=entry['sha256']:raise ValueError('视频摘要不同')
    if target.exists():
        with np.load(target,allow_pickle=False) as a:
            if json.loads(str(a['metadata']))!=identity:raise ValueError('已有世界坐标缓存身份不同')
            return {k:a[k].copy() for k in a.files if k!='metadata'},True
    arrays={k:[] for k in ('timestamps','world','visible','people','latency_ms')}
    with PoseService(model_path=heavy,video=True) as pose,av.open(str(path)) as video:
        video.seek(start*1000);previous=-1
        for frame in video.decode(video=0):
            stamp=round(float(frame.time)*1000)
            if stamp<start or stamp<=previous:continue
            if stamp>=end:break
            previous=stamp;rgb=frame.to_ndarray(format='rgb24');h,w=rgb.shape[:2]
            left,top,right,bottom=detect_content_bounds(rgb)
            rgb=np.ascontiguousarray(rgb[round(top*h):round(bottom*h),round(left*w):round(right*w)])
            began=time.perf_counter();r=pose.landmarker.detect_for_video(pose.mp.Image(image_format=pose.mp.ImageFormat.SRGB,data=rgb),stamp)
            elapsed=(time.perf_counter()-began)*1000;world=np.full((13,3),np.nan);visible=np.zeros(13,bool)
            if len(r.pose_landmarks)==1 and len(r.pose_world_landmarks)==1:
                world=np.asarray([[r.pose_world_landmarks[0][i].x,r.pose_world_landmarks[0][i].y,r.pose_world_landmarks[0][i].z] for i in JOINTS],np.float32)
                visible=np.asarray([min(r.pose_landmarks[0][i].visibility,r.pose_landmarks[0][i].presence)>=.5 and
                      0<=r.pose_landmarks[0][i].x<=1 and 0<=r.pose_landmarks[0][i].y<=1 for i in JOINTS])
            for k,v in dict(timestamps=stamp,world=world,visible=visible,people=len(r.pose_landmarks),latency_ms=elapsed).items():arrays[k].append(v)
    arrays={k:np.asarray(v) for k,v in arrays.items()};np.savez_compressed(target,metadata=json.dumps(identity,sort_keys=True),**arrays)
    return arrays,False


def evaluate(net,labels,mean,std,arrays):
    reports=[]
    for step in (1,3):
        indices=np.arange(0,len(arrays['timestamps']),step);windows=[];rejected=0
        for start in range(0,len(indices)-31,16):
            ix=indices[start:start+32];world=arrays['world'][ix];visibility=arrays['visible'][ix]
            # 缺失整个人或核心肩髋证据时拒判；不照搬上游对大缺口伸展插值。
            if not np.isfinite(world).all() or np.any(arrays['people'][ix]!=1) or np.mean(visibility[:,[1,2,7,8]])<.8:
                rejected+=1;continue
            x=(world.reshape(32,39)-mean)/std
            began=time.perf_counter()
            with torch.inference_mode():scores=torch.softmax(net(torch.from_numpy(x.astype(np.float32))[None]),-1)[0].numpy()
            order=np.argsort(scores)[::-1];windows.append({'start_ms':int(arrays['timestamps'][ix[0]]),'end_ms':int(arrays['timestamps'][ix[-1]]),
                'top':labels[int(order[0])],'score':float(scores[order[0]]),'margin':float(scores[order[0]]-scores[order[1]]),
                'all_joints_visible_fraction':float(visibility.mean()),'model_ms':round((time.perf_counter()-began)*1000,3)})
        from collections import Counter
        reports.append({'sampling_stride':step,'accepted_windows':len(windows),'rejected_windows':rejected,
                        'top_labels':dict(Counter(w['top'] for w in windows)),'windows':windows})
    return reports


def run(variant='heavy'):
    net,labels,mean,std=model();heavy=ROOT/f'data/models/pose_landmarker_{variant}.task'
    info=json.loads((heavy.with_suffix('.json') if variant=='heavy' else heavy.with_name('manifest.json')).read_text())
    if hashlib.sha256(heavy.read_bytes()).hexdigest()!=info['sha256']:raise ValueError('Heavy模型摘要不同')
    entries={e['id']:e for e in json.loads((ROOT/'data/datasets/development-movements/sources.json').read_text(encoding='utf-8'))['sources']}
    results=[]
    for case in CASES:
        arrays,reused=extract(case,entries[case[0]],heavy);result={'source_id':case[0],'source_action':case[1],'frames':len(arrays['timestamps']),
            'pose_latency_p50_ms':float(np.median(arrays['latency_ms'])),'reused':reused,'evaluations':evaluate(net,labels,mean,std,arrays)}
        results.append(result);print(json.dumps({k:v for k,v in result.items() if k!='evaluations'}|{'decisions':[r['top_labels'] for r in result['evaluations']]}),flush=True)
    path=OUT/'skelgym-comparison.json'
    report=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    if 'cases' in report and 'variants' not in report:report={'variants':{'heavy':report.pop('cases')}}
    report.update(scope='offline_development_comparison',production_enabled=False,labels=labels)
    report.setdefault('variants',{})[variant]=results
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--variant',choices=['full','heavy'],default='heavy');run(parser.parse_args().variant)
