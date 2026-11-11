"""只补杠铃姿态真实输入验证；复用原姿态缓存，独立真值缺失不计算准确率。"""
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment,project_path
configure_environment()
import numpy as np
from fitness_app.frame_pipeline import analysis_framing as framing
from fitness_app.media import video_frames
from fitness_app.pose_input import ContentPoseService,input_policy_snapshot
from fitness_app.posture_engine import create_analysis_engine
from fitness_app.posture_rules import policy_version
from fitness_app.research_extract import probe_research_video
from fitness_app.sessions import IdentityContinuity
from fitness_app.research_data import load_dataset
from diagnose_cycles import cached_frames
from diagnose_barbell_row import prepare_frames

OUT=ROOT/'data/verification/step-19/barbell-posture-review.json'


def current_pose(path,action,cache_name,segment_ms=None):
    """新增片段只推理一次；规则修改不使原始姿态缓存失效。"""
    path=project_path(path);cache=project_path(ROOT/'data/verification/step-19'/cache_name)
    identity={'source_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'source_bytes':path.stat().st_size,
              'source_modified_ns':path.stat().st_mtime_ns,'policy':input_policy_snapshot(ContentPoseService)}
    if segment_ms is not None:
        if len(segment_ms)!=2 or any(type(v) is not int for v in segment_ms) or not 0<=segment_ms[0]<segment_ms[1]:raise ValueError('固定源片段时间无效')
        identity['segment_ms']=list(segment_ms)
    if cache.exists():
        with np.load(cache,allow_pickle=False) as archive:
            if json.loads(str(archive['metadata']))!=identity:raise ValueError('真实姿态缓存身份不匹配，不覆盖旧缓存')
            arrays={k:archive[k].copy() for k in ('timestamps_ms','points','sizes','person_counts','bounds')}
        hit=True
    else:
        arrays={k:[] for k in ('timestamps_ms','points','sizes','person_counts','bounds')}
        with ContentPoseService(video=True) as model:
            for stamp,rgb in video_frames(path,probe_research_video(path)):
                if segment_ms is not None and stamp<segment_ms[0]:continue
                if segment_ms is not None and stamp>=segment_ms[1]:break
                detected=model.detect(rgb,stamp);people=detected['poses'];height,width=rgb.shape[:2]
                raw=np.full((33,5),np.nan,dtype=np.float32)
                if len(people)==1:
                    raw=np.asarray([[p[k] for k in ('x','y','z','visibility','presence')] for p in people[0]],dtype=np.float32)
                for key,value in {'timestamps_ms':stamp,'points':raw,'sizes':(width,height),
                                  'person_counts':len(people),'bounds':detected['content_bounds']}.items():arrays[key].append(value)
        with cache.open('xb') as output:np.savez_compressed(output,metadata=json.dumps(identity,sort_keys=True),**{k:np.asarray(v) for k,v in arrays.items()})
        hit=False
    frames=[];continuity=IdentityContinuity()
    for i,stamp in enumerate(arrays['timestamps_ms']):
        count=int(arrays['person_counts'][i]);raw=arrays['points'][i]
        poses=[[dict(zip(('x','y','z','visibility','presence'),map(float,p))) for p in raw]] if count==1 else [[] for _ in range(count)]
        quality=framing(action,{'poses':poses,'content_bounds':list(map(float,arrays['bounds'][i]))})
        frame={'captured_ms':int(stamp),'frame_seq':i,'width':int(arrays['sizes'][i][0]),'height':int(arrays['sizes'][i][1]),'framing':quality}
        frame['identity_accepted']=count==1 and continuity.accept(frame);frames.append(frame)
    return frames,{'path':path.relative_to(ROOT).as_posix(),'source_sha256':identity['source_sha256'],
                   'pose_cache':cache.relative_to(ROOT).as_posix(),'cache_hit':hit,'pose_mode':'actual_full_content_roi',
                   'new_inference_frames':0 if hit else len(frames),'input_policy':identity['policy'],
                   **({'segment_ms':list(segment_ms)} if segment_ms is not None else {})}


def evaluate(action,frames,view):
    engine=create_analysis_engine(action);events=[];episodes={};unusable=0;phase_reasons=Counter()
    if action in {'barbell_bent_over_row','conventional_deadlift'} and not engine.counting_available:
        raise ValueError('原冻结计数资源不可用，不能用空事件冒充真实姿态检查')
    for frame in frames:
        if not frame['framing']['usable'] or not frame.get('identity_accepted',True):
            engine.reset('tracking_lost');unusable+=1;continue
        output=engine.process(frame,{'coaching_view':view,'stream_epoch':'real-posture-review'})
        events.extend(output['events'])
        for item in output.get('posture_findings',[]):episodes[(item['code'],item['side'],item['start_ms'])]=item
        sides=output.get('observation',{}).get('sides',{})
        for state in sides.values():
            if state.get('reason'):phase_reasons[state['reason']]+=1
    findings=[{**i,'side':e['side'],'kind':'cycle'} for e in events for i in e.get('assessments',[])]+list(episodes.values())
    statuses=Counter(i['status'] for i in findings)
    return {'action_id':action,'frames':len(frames),'unusable_frames':unusable,'events':events,'findings':findings,
            'status_counts':dict(statuses),'phase_reason_counts':dict(phase_reasons),
            'assessment_state':'evidence_insufficient' if not any(i['status'] in {'issue','not_observed'} for i in findings) else 'reference_assessed',
            'accuracy':None,'accuracy_reason':'没有独立具体错误／时间标签；来源粗标签和本次图像复核均不替代真值'}


def render_review(report,frame_sets):
    """原片帧与关键点叠图只辅助开发复核，不制作独立真值。"""
    import av
    from PIL import Image,ImageDraw,ImageFont,ImageOps
    font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',18)
    sheet=Image.new('RGB',(1100,1350),'white');draw=ImageDraw.Draw(sheet)
    draw.text((20,12),'真实帧与模型投影复核：辅助排错，不能作为独立准确率真值',font=font,fill='black')
    specs=[('press','肩推：起始位／短暂偏移', [800,1700]),
           ('row','划船：双侧投影冲突，应拒判',[17251,17951]),
           ('deadlift_current_input','硬拉PB：只能评估顶端伸展',[1300,1700])]
    panels=[]
    for row,(key,title,targets) in enumerate(specs):
        item=report[key];frames=frame_sets[key]
        selected=[min(frames,key=lambda f:abs(f['captured_ms']-target)) for target in targets]
        wanted={f['captured_ms']:f for f in selected};images={}
        with av.open(str(ROOT/item['path'])) as container:
            for decoded in container.decode(video=0):
                stamp=round(float(decoded.pts*decoded.time_base)*1000)
                if stamp not in wanted:continue
                frame=wanted[stamp];p=frame['framing']['landmarks'];image=Image.fromarray(decoded.to_ndarray(format='rgb24'));g=ImageDraw.Draw(image)
                for indices,color in (((11,13,15),'cyan'),((12,14,16),'orange'),((11,23,25,27),'cyan'),((12,24,26,28),'orange')):
                    for a,b in zip(indices,indices[1:]):
                        if len(p)>max(a,b) and p[a].get('valid') and p[b].get('valid'):
                            g.line([(p[i]['x']*decoded.width,p[i]['y']*decoded.height) for i in (a,b)],fill=color,width=3)
                images[stamp]=ImageOps.contain(image,(530,355))
        for column,frame in enumerate(selected):
            x=15+column*550;y=48+row*420;image=images[frame['captured_ms']]
            sheet.paste(image,(x+(530-image.width)//2,y))
            draw.text((x,y+360),title+' · '+str(frame['captured_ms'])+'ms',font=font,fill='black')
            panels.append({'source_path':item['path'],'captured_ms':frame['captured_ms'],'review_only':True})
    draw.text((20,1300),'青色＝模型左侧，橙色＝模型右侧；图像方向和关节投影不等于脊柱或肌肉测量。',font=font,fill='black')
    image_path=ROOT/'data/verification/step-19/barbell-posture-review.png';sheet.save(image_path)
    return {'path':image_path.relative_to(ROOT).as_posix(),'panels':panels,'independent_ground_truth':False}


def run(render=False):
    # 单片段接线成功沿用，原有默认 Full／保存链不再重复运行。
    prior=json.loads((ROOT/'data/verification/step-19/backend-posture.json').read_text(encoding='utf-8'))
    row_frames,_,row_hit=prepare_frames()
    row=evaluate('barbell_bent_over_row',row_frames,'oblique')
    row.update(path='data/datasets/development-movements/barbell-row-dvids-639914-side.mp4',
               pose_cache='data/verification/step-14/dvids-639914-side-poses.npz',cache_hit=row_hit,
               pose_mode='existing_real_full_pose_cache_with_verified_content_bounds',new_inference_frames=0)
    front=ROOT/'data/datasets/development-movements/overhead-press-dvids-692514-front.mp4'
    press_frames,meta=current_pose(front,'barbell_overhead_press','military-press-front-poses.npz')
    press={**evaluate('barbell_overhead_press',press_frames,'front'),**meta,
           'framing_scope_counts':dict(Counter(f['framing'].get('posture_scope','action_required_parts') for f in press_frames)),
           'variant_verified':False,'scope':'原始正面板；腿脚裁切，仅验证可见上肢肘腕关系，不验证全身站姿或次数'}
    samples={s.path:s for s in load_dataset()}
    baseline=json.loads((ROOT/'data/experiments/baseline.json').read_text(encoding='utf-8'))
    old=json.loads((ROOT/'data/verification/step-12/development-cycles.json').read_text(encoding='utf-8'))
    lifts=[]
    for case in old['actions']['conventional_deadlift']['cases']:
        sample=samples[case['source_path']]
        if sample.partition!='development':raise ValueError('仅验证原固定开发片段，不能挑独立测试集调规则')
        if sample.sha256==prior['source_sha256']:
            lifts.append({'path':case['source_path'],'reused_passed_evidence':'data/verification/step-19/backend-posture.json',
                          'findings':prior['findings'],'events_count':prior['count'],'frames':prior['frames'],
                          'original_rule_version':prior['findings'][0]['rule_version'],'new_inference_frames':0})
            continue
        frames=cached_frames(sample,baseline)
        item=evaluate('conventional_deadlift',frames,'side')
        item.update(path=case['source_path'],source_sha256=sample.sha256,partition=sample.partition,
                    pose_mode='existing_real_full_pose_cache_with_verified_content_bounds',new_inference_frames=0,
                    source_label_not_extension_truth=sample.label)
        lifts.append(item)
    # PB仅因已有完整周期而选作端到端补验；不将PB当作伸展不足标签。
    sample=samples['mydeadlift/Punggung Bungkuk/PB_S06_R26.mp4']
    frames,meta=current_pose(sample.absolute_path,'conventional_deadlift','deadlift-pb-s06-r26-poses.npz')
    current_lift={**evaluate('conventional_deadlift',frames,'side'),**meta,'source_label_not_extension_truth':sample.label}
    report={'schema_version':1,'policy_version':policy_version(),'row':row,'press':press,
            'deadlift_fixed_development_cases':lifts,'deadlift_current_input':current_lift,
            'independent_accuracy':None,'overall_posture_correctness':None,
            'unsupported_errors':{'barbell_overhead_press':['全程杠铃轨迹','腰部代偿','握距是否适合本人'],
                                  'barbell_bent_over_row':['腰椎曲度','器械负重'],
                                  'conventional_deadlift':['脊柱局部弯曲／原PB错误标签','肌肉发力']},
            'scope':'真实视频／缓存诊断；不重复既有计数测试、5分钟负载或冻结分类实验；不计算伪准确率'}
    if OUT.exists():
        previous=json.loads(OUT.read_text(encoding='utf-8'))
        for key in ('screening_review','visual_review','api_verification'):
            if key in previous:report[key]=previous[key]
    if render:report['visual_review']=render_review(report,{'press':press_frames,'row':row_frames,'deadlift_current_input':frames})
    OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    for name,item in [('row',row),('press',press),('deadlift_current',current_lift)]:
        print(json.dumps({'name':name,'frames':item['frames'],'events':len(item['events']),
                          'statuses':item['status_counts'],'unusable':item['unusable_frames'],
                          'new_pose_frames':item['new_inference_frames']},ensure_ascii=False),flush=True)
    print(json.dumps({'report':str(OUT),'rule_version':policy_version()},ensure_ascii=False))


def current_barbell_input_check():
    """仅为已变更输入策略复查三个既定源，原姿态与报告保留，按身份复用。"""
    from fitness_app.skeleton import skeleton_snapshot
    report=json.loads(OUT.read_text(encoding='utf-8'))
    cases=(('press','barbell_overhead_press','front'),('row','barbell_bent_over_row','oblique'),
           ('deadlift_current_input','conventional_deadlift','side'))
    policy=input_policy_snapshot(ContentPoseService);results={}
    for key,action,view in cases:
        path=project_path(ROOT/report[key]['path'])
        identity={'source_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'policy':policy}
        # 处理策略不同必须分开缓存；命名取内容身份，不覆盖旧证据。
        token=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()[:24]
        frames,meta=current_pose(path,action,'barbell-'+token+'.npz')
        outcome=evaluate(action,frames,view)
        visible=0;edges=0
        for frame in frames:
            displayed=frame if frame.get('identity_accepted',True) else {**frame,'framing':{**frame['framing'],'code':'TARGET_AMBIGUOUS'}}
            value=skeleton_snapshot(displayed,time_base='source_ms')
            indices={p['index'] for p in value['joints']}
            if value['available']:visible+=1
            if any(not set(edge)<=indices for edge in value['connections']):raise ValueError('骨架跨缺失点连线')
            if displayed['framing']['code']=='TARGET_AMBIGUOUS' and value['available']:raise ValueError('歧义目标泄露骨架')
            edges+=len(value['connections'])
        if any(not f.get('message') or not f.get('suggestion') for f in outcome['findings'] if f['status']=='issue'):
            raise ValueError('真实问题缺少说明或建议')
        results[key]={**outcome,**meta,'skeleton_frames':visible,'visible_edge_total':edges,
                      'previous_events':len(report[key]['events']),
                      'previous_unusable_frames':report[key]['unusable_frames']}
        print(json.dumps({'case':key,'frames':len(frames),'new_inference':meta['new_inference_frames'],
                          'events':len(outcome['events']),'unusable':outcome['unusable_frames'],
                          'statuses':outcome['status_counts'],'skeleton_frames':visible},ensure_ascii=False),flush=True)
    report['current_input_recheck']={'input_policy':policy,'posture_policy':policy_version(),'cases':results,
        'implementation_sha256':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in
            ('fitness_app/skeleton.py','fitness_app/coaching.py','scripts/validate_real_posture.py')},
        'scope':'原三个固定真实片段的策略2接续；不调整阈值、不读独立测试，不据骨架或来源粗标签计算准确率'}
    OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def crop_stability_check():
    """只复查已固定的 CDC 下降片段，保留旧策略缓存，不扩充准确率分母。"""
    import inspect
    from PIL import Image,ImageDraw,ImageFont
    from fitness_app.skeleton import skeleton_snapshot
    path=ROOT/'data/datasets/development-movements/push-up-cdc-home.webm'
    baseline=ROOT/'data/verification/step-19/push-up-cdc-descent-poses.npz'
    report_path=ROOT/'data/verification/step-19/account-coaching.json'
    image_path=ROOT/'data/verification/step-19/cdc-skeleton-overlay.png'
    identity={'source_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
              'baseline_sha256':hashlib.sha256(baseline.read_bytes()).hexdigest(),
              'input_policy':input_policy_snapshot(ContentPoseService),'posture_policy':policy_version(),
              'script_sha256':hashlib.sha256(inspect.getsource(crop_stability_check).encode()).hexdigest(),
              'skeleton_sha256':hashlib.sha256((ROOT/'fitness_app/skeleton.py').read_bytes()).hexdigest(),
              'segment_ms':[65800,70800]}
    report=json.loads(report_path.read_text(encoding='utf-8'))
    prior=report.get('crop_stability_check',{})
    if prior.get('identity')==identity and image_path.exists() and prior.get('image_sha256')==hashlib.sha256(image_path.read_bytes()).hexdigest():
        print(json.dumps({'cache_hit':True,**prior},ensure_ascii=False));return
    with np.load(baseline,allow_pickle=False) as archive:
        old_identity=json.loads(str(archive['metadata']))
        if old_identity['source_sha256']!=identity['source_sha256']:raise ValueError('基线来源不匹配')
        old_sizes=archive['sizes'];old_bounds=archive['bounds'];old_stamps=archive['timestamps_ms'].tolist()
        old_boxes=[tuple(round(float(v)*int(size[i%2])) for i,v in enumerate(bounds)) for size,bounds in zip(old_sizes,old_bounds)]
    frames=[];transforms=[];sample=None;continuity=IdentityContinuity()
    with ContentPoseService(video=True) as model:
        for stamp,rgb in video_frames(path,probe_research_video(path)):
            if stamp<65800:continue
            if stamp>=70800:break
            output=model.detect(rgb,stamp);height,width=rgb.shape[:2]
            frame={'captured_ms':stamp,'frame_seq':len(frames),'width':width,'height':height,
                   'model_version':output['model'],'framing':framing('standard_push_up',output)}
            frame['identity_accepted']=continuity.accept(frame);frames.append(frame);transforms.append(output['input_transform'])
            if len(frames)==75:sample=(rgb.copy(),frame)
    if [f['captured_ms'] for f in frames]!=old_stamps:raise ValueError('新旧源帧不一致')
    outcome=evaluate('standard_push_up',frames,'side')
    if outcome['events']:raise ValueError('下降半周期不应计作完整动作')
    if any(t['tracker_reset'] for t in transforms):raise ValueError('固定片段仍有跟踪重建')
    rgb,frame=sample;overlay=skeleton_snapshot(frame,time_base='source_ms')
    font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',18)
    image=Image.fromarray(rgb).resize((640,480));draw=ImageDraw.Draw(image);points={p['index']:p for p in overlay['joints']}
    for a,b in overlay['connections']:
        draw.line([(points[i]['x']*640,points[i]['y']*480) for i in (a,b)],fill='#29e0af',width=4)
    for point in points.values():
        x,y=point['x']*640,point['y']*480;draw.ellipse((x-4,y-4,x+4,y+4),fill='#ffdd70')
    sheet=Image.new('RGB',(680,600),'#152023');sheet.paste(image,(20,40));draw=ImageDraw.Draw(sheet)
    draw.text((20,10),'真实视频帧 + 后端骨架输出',font=font,fill='white')
    draw.text((20,530),f"源时间 {frame['captured_ms']} ms · 仅下降片段，完整次数 0",font=font,fill='white')
    draw.text((20,562),'来源 CDC / Wikimedia Commons · 公有领域 · 非纠错准确率证据',font=font,fill='white')
    sheet.save(image_path)
    result={'identity':identity,'frames':len(frames),'new_full_inference_frames':len(frames),
            'baseline_geometry_changes':sum(a!=b for a,b in zip(old_boxes,old_boxes[1:])),
            'tracker_resets':sum(t['tracker_reset'] for t in transforms),
            'stabilized_frames':sum(t['crop_stabilized'] for t in transforms),
            'framing_counts':dict(Counter(f['framing']['code'] for f in frames)),
            'complete_events':len(outcome['events']),'accuracy':None,
            'image':image_path.relative_to(ROOT).as_posix(),'image_sha256':hashlib.sha256(image_path.read_bytes()).hexdigest(),
            'scope':'固定下降半周期的裁边稳定与骨架接线；不证明完整周期召回或纠错准确性'}
    report['crop_stability_check']=result
    report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'cache_hit':False,**result},ensure_ascii=False))


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--render',action='store_true')
    parser.add_argument('--crop-stability',action='store_true')
    parser.add_argument('--current-barbell-input',action='store_true')
    args=parser.parse_args()
    if args.current_barbell_input:current_barbell_input_check()
    elif args.crop_stability:crop_stability_check()
    else:run(args.render)
