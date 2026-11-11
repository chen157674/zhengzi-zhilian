"""原视频→真实输入服务→原目标连续性门控→项目姿态引擎的开发回放。"""
from collections import Counter
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment,project_path
configure_environment()
os.environ['FITNESS_PRETRAINED_CORRECTION']='1'
from fitness_app.gym_pose import GymPoseService
from fitness_app.media import probe_video,video_frames
from fitness_app.frame_pipeline import analysis_framing
from fitness_app.sessions import IdentityContinuity
from fitness_app.posture_engine import create_analysis_engine
from fitness_app.posture_rules import policy_version
from fitness_app.coaching import coaching_snapshot,coaching_observation,committed_issues
from fitness_app.demo_feedback import build_demo_feedback
from scripts.prepare_pretrained_correction import atomic_json,sha


def review(output_name='pretrained-video-review',refresh_demos=False):
    import numpy as np
    from PIL import Image,ImageDraw,ImageFont
    if output_name not in ('pretrained-video-review','body-lean-review','movement-guidance-review'):raise ValueError('验证输出名称无效')
    if refresh_demos and output_name not in ('body-lean-review','movement-guidance-review'):raise ValueError('仅身体侧倾复核可刷新本次演示反馈')
    if output_name in ('body-lean-review','movement-guidance-review'):
        os.environ['FITNESS_PRETRAINED_CORRECTION']='0';os.environ['FITNESS_BODY_LEAN']='1'
    os.environ['FITNESS_MOVEMENT_GUIDANCE']='1' if output_name=='movement-guidance-review' else '0'
    target=ROOT/'data/verification/round3'/f'{output_name}.json'
    history=[]
    if target.exists():
        old=json.loads(target.read_text(encoding='utf-8'));history=old.get('previous_reviews',[])
        history.append({'policy_version':old['policy_version'],'videos':[{key:v[key] for key in
            ('number','frames_count','identity_accepted_frames','status_counts','base_gate_counts')} for v in old['videos']]})
    manifest=ROOT/'data/presentation-assets.json'
    assets=json.loads(manifest.read_text(encoding='utf-8'));results=[];panels=[];updates=[]
    if refresh_demos:
        for number in ('1694','1693','1692','1575'):
            asset=assets['demos']['user-'+number];record=asset['track'];path=project_path(record['path'])
            if path!=(ROOT/'fitness_app/static/demo'/f'user-{number}-track.json').resolve() or sha(path)!=record['sha256'] or path.stat().st_size!=record['bytes']:
                raise ValueError('演示反馈文件身份不符，停止刷新')
    policy_version.cache_clear();font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',15)
    for number in ('1694','1693','1692','1575'):
        asset=assets['demos']['user-'+number];source=project_path(asset['video']['path'])
        if sha(source)!=asset['video']['sha256']:raise ValueError('原视频SHA不符')
        info=probe_video(source);engine=create_analysis_engine(asset['action_id']);identity=IdentityContinuity()
        last=-1;frames=[];status=Counter();gates=Counter();begin=time.perf_counter();chosen=None;chosen_label=None;good_frames=0
        chosen_body=chosen_lean=None;last_feedback=[];feedback_stamp=0
        demo_frames=[];all_status=Counter()
        from fitness_app.pretrained_quality import PretrainedObserver
        control=PretrainedObserver('machine_chest_press',engine.version,codes={'machine_chest_press.pretrained_upper_body_balance'}) if number=='1575' else None
        with GymPoseService(video=True) as pose:
            for stamp,rgb in video_frames(source,info):
                if stamp//100==last:continue
                last=stamp//100;result=pose.detect(rgb,stamp);framing=analysis_framing(asset['action_id'],result)
                frame={'captured_ms':stamp,'frame_seq':len(frames),'width':rgb.shape[1],'height':rgb.shape[0],
                       'framing':framing,'pretrained':result.get('pretrained')}
                good=framing['usable'] and identity.accept(frame);items=[];output={}
                frame['identity_accepted']=bool(good)
                if good:
                    good_frames+=1
                    output=engine.process(frame,{'coaching_view':'auto','stream_epoch':'pretrained-video-'+number})
                    items=[item for item in output['observation']['posture']['items'] if '.pretrained_' in item['code'] or output_name=='movement-guidance-review']
                    for item in items:status[item['code']+':'+item['status']]+=1
                else:
                    gates[framing['code']]+=1;identity.reset();engine.reset('tracking_lost')
                data=result.get('pretrained') or {}
                pending=committed_issues(output)
                if pending:last_feedback=pending;feedback_stamp=stamp
                frame['analysis']=coaching_observation(output)
                cue=coaching_snapshot({'action_id':asset['action_id'],'status':'running','supported_items':engine.rules,
                                       'feedback_persistence':'memory_only'},frame,last_feedback,stamp-feedback_stamp)
                feedback=build_demo_feedback(frame,output,engine.rules,cue)
                for item in output.get('observation',{}).get('posture',{}).get('items',[]):all_status[item['code']+':'+item['status']]+=1
                if feedback['state']=='clear' and number!='1575':
                    feedback['clear_label']='你标记的问题尚未检出' if asset.get('user_reported_issues') else '已检查项目未见偏差'
                control_status=None
                if control is not None:
                    if good:control_status=control.update(frame,{'coaching_view':'auto'})[0]['status']
                    else:control.reset()
                if refresh_demos:
                    points=framing.get('landmarks',[])
                    values=[[round(p['x'],6),round(p['y'],6)] if p.get('valid') else None for p in points] if len(points)==33 and good else [None]*33
                    demo_frames.append({'t':stamp,'p':values,'people':framing['person_count'],'cue':cue.get('primary_cue'),'feedback':feedback})
                frames.append({'t':stamp,'base_framing':framing['code'],'base_people':framing['person_count'],
                    'identity_accepted':bool(good),'body_features':data.get('body_features'),
                    'body':data.get('body') if output_name in ('body-lean-review','movement-guidance-review') else None,
                    'original_landmarks':framing.get('landmarks') if output_name=='movement-guidance-review' else None,
                    'cue_state':cue['state'],'cue_code':(cue.get('primary_cue') or {}).get('code'),
                    'visible_feedback_state':feedback['state'],'highlighted':feedback.get('highlight') is not None,
                    'body_projection_control_status':control_status,
                    'hands':[{'bbox':h['bbox'],'score':h['score'],'body_wrist_index':h['body_wrist_index'],
                              'observation':h.get('observation_with_body_wrist'),
                              'keypoints':h.get('keypoints') if output_name=='movement-guidance-review' else None} for h in data.get('hands',[])],
                    'hand_reason':data.get('hand_reason'),'raw_hand_candidates':data.get('raw_hand_candidates'),
                    'items':items})
                issues=[item for item in items if item['status']=='issue']
                if chosen is None and stamp>=info['duration_ms']*.25 or feedback['state']=='issue' and chosen_label is None:
                    chosen=np.array(rgb,copy=True)
                    chosen_body=data.get('body');chosen_lean=data.get('body_features',{}).get('scene_lean')
                    if feedback['state']=='issue':chosen_label=feedback['title']
                if len(frames)%100==0:print(json.dumps({'video':number,'source_seconds':round(stamp/1000,1),'frames':len(frames)}),flush=True)
        result={'number':number,'action_id':asset['action_id'],'source_sha256':asset['video']['sha256'],
            'sample_interval_ms':100,'frames':frames,'frames_count':len(frames),'identity_accepted_frames':good_frames,
            'status_counts':dict(status),'base_gate_counts':dict(gates),'elapsed_seconds':round(time.perf_counter()-begin,2),
            'selected_video_action_not_new_recognition':True}
        result['visible_feedback_counts']=dict(Counter(f['visible_feedback_state'] for f in frames))
        if control is not None:result['body_projection_control_counts']=dict(Counter(f['body_projection_control_status'] for f in frames if f['body_projection_control_status']))
        results.append(result)
        if refresh_demos:
            path=project_path(asset['track']['path']);document=json.loads(path.read_text(encoding='utf-8'))
            document.update(frames=demo_frames,visible_frames=sum(any(p is not None for p in f['p']) for f in demo_frames),
                rule_version=engine.version,feedback_state_counts=result['visible_feedback_counts'],
                posture_status_counts=dict(all_status),feedback_implementation_sha256=sha(ROOT/'fitness_app/demo_feedback.py'),
                source_identity_gated=True,body_lean_only=output_name=='body-lean-review',movement_guidance=output_name=='movement-guidance-review',
                classification_reused=True,posture_action_source='previously_selected_demo_action',
                new_pose_inferences=len(frames),validation_report=target.relative_to(ROOT).as_posix())
            updates.append((asset,path,document))
        if chosen is not None:
            im=Image.fromarray(chosen)
            if chosen_body and chosen_lean and chosen_lean.get('status')=='observed':
                import math
                draw=ImageDraw.Draw(im);w,h=im.size;p=chosen_body['keypoints']
                shoulder=((p[5]['x']+p[6]['x'])*w/2,(p[5]['y']+p[6]['y'])*h/2)
                hip=((p[11]['x']+p[12]['x'])*w/2,(p[11]['y']+p[12]['y'])*h/2)
                draw.line((hip,shoulder),fill='#00e5ff',width=5)
                draw.line((p[5]['x']*w,p[5]['y']*h,p[6]['x']*w,p[6]['y']*h),fill='#00e5ff',width=5)
                angle=math.radians(chosen_lean['reference']['image_up_degrees']);length=math.dist(hip,shoulder)
                draw.line((hip,(hip[0]+math.sin(angle)*length,hip[1]-math.cos(angle)*length)),fill='#f5b942',width=4)
            im.thumbnail((480,270));panel=Image.new('RGB',(480,320),'white');panel.paste(im,(0,0))
            draw=ImageDraw.Draw(panel);draw.text((6,274),number+' '+(chosen_label or '未观察到已确认候选问题'),font=font,fill='#202020')
            panels.append(panel)
        print(json.dumps({k:result[k] for k in ('number','frames_count','identity_accepted_frames','status_counts','base_gate_counts','elapsed_seconds')},ensure_ascii=False),flush=True)
    report={'schema_version':1,'scope':'same_person_previously_reviewed_videos_development_not_independent_accuracy',
        'policy_version':policy_version(),'previous_reviews':history,
        'body_lean_only':output_name=='body-lean-review','movement_guidance':output_name=='movement-guidance-review',
        'review_script_sha256':sha(Path(__file__)),'source_is_original_video_not_manual_hand_crops':True,
        'original_identification_and_counting_input_preserved':True,'videos':results,'production_enabled':False,'api_calls':0}
    atomic_json(target,report)
    sheet=Image.new('RGB',(960,640),'white')
    for i,panel in enumerate(panels):sheet.paste(panel,((i%2)*480,(i//2)*320))
    sheet.save(target.with_suffix('.jpg'),quality=92)
    if refresh_demos:
        for asset,path,document in updates:
            if sha(path)!=asset['track']['sha256']:raise ValueError('回放期间演示反馈已被修改，停止覆盖')
        for asset,path,document in updates:
            atomic_json(path,document)
            asset.update(track={**asset['track'],'bytes':path.stat().st_size,'sha256':sha(path)},
                         frames=len(document['frames']),visible_frames=document['visible_frames'],rule_version=document['rule_version'])
            atomic_json(manifest,assets)
        print(json.dumps({'refreshed_demo_feedbacks':len(updates),'videos_modified':False,'classification_retrained':False}),flush=True)


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',choices=['pretrained-video-review','body-lean-review','movement-guidance-review'],default='pretrained-video-review')
    parser.add_argument('--refresh-demos',action='store_true')
    args=parser.parse_args();review(args.output,args.refresh_demos)
