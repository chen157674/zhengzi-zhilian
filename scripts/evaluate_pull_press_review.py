"""冻结生产误识别窗口的跨组视觉复核；私人视频不外发、不参与梯度。"""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment
configure_environment()
import av
import numpy as np
from fitness_app.video_vlm import VideoActionVLM
from fitness_app.pull_press_review import PullPressReviewer,route,version,CONFIG,PROMPT
from fitness_app.recognition_observations import observations
from fitness_app.research_extract import atomic_json

REPORT=ROOT/'data/verification/round3/pull-press-cross-review.json'


def images(path,times):
    if len(times)!=len(set(times)) or times!=sorted(times):raise ValueError('原图源时间必须有序且唯一')
    def decode(start_ms):
        selected={}
        with av.open(str(path)) as container:
            stream=container.streams.video[0]
            if start_ms:container.seek(int(start_ms/1000/float(stream.time_base)),stream=stream)
            for frame in container.decode(stream):
                if frame.time is None:continue
                stamp=round(float(frame.time)*1000)
                if stamp in wanted:selected[stamp]=frame.to_ndarray(format='rgb24')
                if stamp>times[-1]:break
        return selected
    wanted=set(times);selected=decode(max(0,times[0]-2000))
    # 某些MPEG-4索引seek会跳过目标前的帧；回到开头顺序解码，不以邻帧冒充原图。
    if set(selected)!=wanted:selected=decode(0)
    if set(selected)!=wanted:raise ValueError('原始视频缺少指定的确切源图：'+str(sorted(wanted-set(selected))))
    return [selected[stamp] for stamp in times]


def run():
    previous=json.loads(REPORT.read_text(encoding='utf-8')) if REPORT.exists() else None
    if previous and (previous.get('state')!='completed' or previous.get('version')==version()):
        raise ValueError('跨组复核已有本策略结果或任务未完成，不重复推理')
    old=json.loads((ROOT/'data/verification/round3/lat-pulldown-auto-review.json').read_text(encoding='utf-8'))
    if old['state']!='completed':raise ValueError('等待原自动任务结束，不并行抢同模型')
    manifest=json.loads((ROOT/'data/presentation-assets.json').read_text(encoding='utf-8'))
    base=VideoActionVLM('4b',quantized=True);model=PullPressReviewer(base)
    document={'state':'running','pid':__import__('os').getpid(),'scope':'same_user_development_windows_not_independent_accuracy',
        'policy':CONFIG,'prompt':PROMPT,'version':version(),'base_revision':base.info['revision'],
        'private_media_uploaded':False,'new_training_performed':False,'rows':[],
        **({'previous_reviews':previous.get('previous_reviews',[])+[{k:v for k,v in previous.items() if k!='previous_reviews'}]} if previous else {})}
    atomic_json(REPORT,document)
    for video in old['videos']:
        number=video['demo_id'].split('-')[-1];item=manifest['demos'][video['demo_id']];path=ROOT/item['video']['path']
        if hashlib.sha256(path.read_bytes()).hexdigest()!=video['source_sha256']:raise ValueError('原视频身份变化')
        with gzip.open(ROOT/('data/experiments/open-source-correction/user-'+number+'-native-inputs.json.gz'),'rt',encoding='utf-8') as stream:native=json.load(stream)
        for window in video['windows']:
            times=window['source_ms'];selected=[row for row in native['rows'] if times[0]<=row['t']<=times[-1] and len(row['pose_result']['poses'])==1]
            arrays={'timestamps':np.asarray([r['t'] for r in selected]),
                'points':np.asarray([[[p[k] for k in ('x','y','z','visibility','presence')] for p in r['pose_result']['poses'][0]] for r in selected]),
                'people':np.ones(len(selected),np.int32),'sizes':np.asarray([[r['width'],r['height']] for r in selected])}
            facts,_=observations(arrays,times[0],times[-1]);routed=route(window['result'],facts)
            if not routed:continue
            prior=next((r for r in (previous or {}).get('rows',[]) if r['source_id']==video['demo_id'] and r['source_ms']==times),None)
            began=time.perf_counter();value=prior['candidate'] if prior else model.review(images(path,times),times,facts)
            if prior:value={**value,'model_version':version(),'numeric_visual_output_reused':True}
            row={'source_id':video['demo_id'],'source_sha256':video['source_sha256'],'source_ms':times,
                'baseline':window['result']['action_id'],'routing_observations':facts,'candidate':value,
                'elapsed_seconds':round(time.perf_counter()-began,2)}
            document['rows'].append(row);atomic_json(REPORT,document)
            print(json.dumps({'video':number,'start_ms':times[0],'baseline':row['baseline'],'candidate':value['action_id'],
                'top':value['top_action_id'],'score':value['score'],'margin':value['margin']},ensure_ascii=False),flush=True)
    document['state']='completed';atomic_json(REPORT,document)


def validation():
    from fitness_app.semantic_model import SemanticPredictor
    from scripts.train_chest_expert import cached_baseline
    from fitness_app.paths import project_path
    output=ROOT/'data/verification/round3/pull-press-validation-review.json'
    previous=json.loads(output.read_text(encoding='utf-8')) if output.exists() else None
    if previous and (previous.get('state')!='completed' or previous.get('version')==version()):raise ValueError('跨组验证本策略已有结果或仍在执行')
    folder=ROOT/'data/verification/step-22'
    entries=json.loads((folder/'semantic-video-features.json').read_text(encoding='utf-8'))['entries']
    families={r['id']:r for r in json.loads((folder/'video-family-evaluation.json').read_text(encoding='utf-8'))['rows']}
    calibration={r['id']:r for r in json.loads((folder/'video-release-calibration.json').read_text(encoding='utf-8'))['validation']['predictions']}
    predictor=SemanticPredictor();reviewer=PullPressReviewer(VideoActionVLM('4b',quantized=True))
    selected=[r for r in entries if r['partition']=='validation'];plan=[]
    controls={};control_actions={'machine_chest_press','reverse_pec_deck_fly','lat_pulldown','dumbbell_shoulder_press','barbell_overhead_press','pull_up','assisted_pull_up'}
    for entry in selected:
        base,arrays,times=cached_baseline(entry,predictor,families,calibration,{})
        facts,_=observations(arrays,int(times[0]),int(times[-1]));routed=route(base,facts)
        control=entry['action_id'] in control_actions and controls.get(entry['action_id'],0)<2
        if control:controls[entry['action_id']]=controls.get(entry['action_id'],0)+1
        plan.append((entry,base,arrays,times,facts,routed,control))
    document={'state':'running','pid':__import__('os').getpid(),'version':version(),'scope':'frozen_original_validation_no_gradient_no_threshold_tuning',
        'base_model_version':predictor.info['version'],'private_media_uploaded':False,'test_used':False,
        'planned_validation_ids':[r[0]['id'] for r in plan],'planned_routed_ids':[r[0]['id'] for r in plan if r[5]],
        'planned_control_ids':[r[0]['id'] for r in plan if r[6]],'rows':[],
        **({'previous_reviews':previous.get('previous_reviews',[])+[{k:v for k,v in previous.items() if k!='previous_reviews'}]} if previous else {})}
    atomic_json(output,document)
    for entry,base,arrays,times,facts,routed,control in plan:
        candidate=base;review=None
        if routed or control:
            path=project_path(entry['path'])
            if hashlib.sha256(path.read_bytes()).hexdigest()!=entry['source_sha256']:raise ValueError('原验证媒体变化')
            samples=images(path,list(map(int,times)))
            if entry.get('crop'):
                crops=[]
                for image in samples:
                    h,w=image.shape[:2];left,top,right,bottom=entry['crop']
                    crops.append(np.ascontiguousarray(image[round(top*h):round(bottom*h),round(left*w):round(right*w)]))
                samples=crops
            prior=next((r for r in (previous or {}).get('rows',[]) if r['id']==entry['id'] and r.get('review')),None)
            review=prior['review'] if prior else reviewer.review(samples,times,facts)
            if prior:review={**review,'model_version':version(),'numeric_visual_output_reused':True}
            if routed and review['accepted']:
                candidate={**base,'action_id':review['action_id'],'top_action_id':review['top_action_id'],
                    'score':review['score'],'margin':review['margin']}
            print(json.dumps({'id':entry['id'],'expected':entry['action_id'],'routed':routed,'baseline':base['action_id'],
                'candidate':candidate['action_id'],'control_top':review['top_action_id'],'score':review['score']}),flush=True)
        document['rows'].append({'id':entry['id'],'source_sha256':entry['source_sha256'],'group':entry['group'],
            'expected':entry['action_id'],'routed':routed,'control':control,'baseline':base,'candidate':candidate,'review':review,
            'routing_observations':facts,'source_ms':list(map(int,times))})
        atomic_json(output,document)
    rows=document['rows'];before=sum(r['baseline']['action_id']==r['expected'] for r in rows if r['expected']!='unknown')
    after=sum(r['candidate']['action_id']==r['expected'] for r in rows if r['expected']!='unknown')
    regressions=[r['id'] for r in rows if r['baseline']['action_id']==r['expected'] and r['candidate']['action_id']!=r['expected']]
    false_accepts=[r['id'] for r in rows if r['expected']=='unknown' and r['candidate']['action_id'] is not None]
    document.update(state='completed',correct_before=before,correct_after=after,regressions=regressions,
        negative_false_accepts=false_accepts,safe_nonregression_candidate=after>=before and not regressions and not false_accepts)
    atomic_json(output,document)
    print(json.dumps({k:document[k] for k in ('state','correct_before','correct_after','regressions','negative_false_accepts','safe_nonregression_candidate')}),flush=True)


def user_controls():
    from fitness_app.gym_pose import GymPoseService
    from fitness_app.frame_pipeline import analysis_framing
    from fitness_app.semantic_recognition import point_array
    from fitness_app.semantic_model import SemanticPredictor
    output=ROOT/'data/verification/round3/pull-press-user-controls.json'
    if output.exists():raise ValueError('真实对照已存在，不重复推理')
    manifest=json.loads((ROOT/'data/presentation-assets.json').read_text(encoding='utf-8'))
    predictor=SemanticPredictor();reviewer=PullPressReviewer(predictor._video())
    document={'state':'running','pid':__import__('os').getpid(),'version':version(),
        'scope':'actual_user_chest_and_fly_counterexamples_not_independent_accuracy','private_media_uploaded':False,'rows':[]}
    atomic_json(output,document)
    for id in ('user-1694','user-1575'):
        item=manifest['demos'][id];path=ROOT/item['video']['path']
        if hashlib.sha256(path.read_bytes()).hexdigest()!=item['video']['sha256']:raise ValueError('用户对照原片改变')
        for start in (1000,6500,12000):
            times=np.arange(start,start+3200,400,dtype=np.int64);samples=images(path,times.tolist())
            rows=[];world=[];people=[];sizes=[]
            with GymPoseService(video=True) as pose:
                for image,stamp in zip(samples,times):
                    result=pose.detect(image,int(stamp));q=analysis_framing(item['action_id'],result)
                    rows.append(point_array(q.get('landmarks',[])));people.append(q['person_count']);sizes.append((image.shape[1],image.shape[0]))
                    world.append(point_array(result['world_poses'][0] if len(result.get('world_poses',[]))==1 else []))
            arrays={'timestamps':times,'points':np.asarray(rows),'world':np.asarray(world),'people':np.asarray(people),'sizes':np.asarray(sizes)}
            base=predictor.predict(samples,times,arrays);facts,_=observations(arrays,int(times[0]),int(times[-1]))
            routed=route(base,facts);review=reviewer.review(samples,times,facts)
            candidate=review['action_id'] if routed and review['accepted'] else base['action_id']
            value={'source_id':id,'source_sha256':item['video']['sha256'],'expected':item['action_id'],
                'source_ms':times.tolist(),'baseline':base,'routing_observations':facts,'routed':routed,'review':review,'candidate_action':candidate}
            document['rows'].append(value);atomic_json(output,document)
            print(json.dumps({'id':id,'start_ms':start,'expected':item['action_id'],'baseline':base['action_id'],
                'routed':routed,'candidate':candidate,'review_top':review['top_action_id']},ensure_ascii=False),flush=True)
    document.update(state='completed',regressions=[r['source_id']+':'+str(r['source_ms'][0]) for r in document['rows']
        if r['baseline']['action_id']==r['expected'] and r['candidate_action']!=r['expected']])
    atomic_json(output,document)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--validation',action='store_true');parser.add_argument('--user-controls',action='store_true')
    args=parser.parse_args()
    if args.validation:validation()
    elif args.user_controls:user_controls()
    else:run()

