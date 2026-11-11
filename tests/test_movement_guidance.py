"""五项扩展的几何/时间/保存边界；合成姿态不当作真实纠错准确率。"""
import copy
import math
import os
import unittest
from unittest.mock import patch
import numpy as np
from fitness_app.movement_guidance import MovementGuidance,ReturnPhase,lag_observation,wrist_observation,frame_measurements
from fitness_app.movement_guidance_policy import RULES,SYNC_SPECS
from fitness_app.posture_rules import policy_version,rules_for


def frame(stamp,left=100,right=100,drift=0,bend=None,translation=0,scale=1.,view='oblique'):
    points=[{'x':.5,'y':.2,'z':0.,'visibility':1.,'presence':1.,'valid':True} for _ in range(33)]
    for side,offset,x,flexion in (('left',0,.35,left),('right',1,.65,right)):
        shoulder=np.array([x,.3]);hip=np.array([x,.75]);elbow=shoulder+np.array([drift if side=='left' else -drift,.2])
        vector=(shoulder-elbow)/np.linalg.norm(shoulder-elbow);theta=math.radians(flexion*(1 if side=='left' else -1))
        rotate=np.array([[math.cos(theta),-math.sin(theta)],[math.sin(theta),math.cos(theta)]])
        wrist=elbow+rotate@vector*.18
        for index,xy in zip((11+offset,13+offset,15+offset,23+offset),(shoulder,elbow,wrist,hip)):
            xy=(xy-.5)*scale+.5+np.array([translation,0])
            points[index].update(x=float(xy[0]),y=float(xy[1]))
    f={'captured_ms':stamp,'frame_seq':stamp//100,'width':1000,'height':1000,'posture_view':view,'identity_accepted':True,
       'framing':{'code':'VISIBLE','usable':True,'person_count':1,'landmarks':points}}
    if bend is not None:
        from fitness_app.rtmpose_input import COCO_TO_MEDIAPIPE
        body=[{'x':points[COCO_TO_MEDIAPIPE[i]]['x'],'y':points[COCO_TO_MEDIAPIPE[i]]['y'],'score':.9,'coordinate_valid':True} for i in range(17)]
        hands=[]
        for offset,wrist_index in ((0,9),(1,10)):
            wrist=np.array([points[15+offset]['x'],points[15+offset]['y']]);elbow=np.array([points[13+offset]['x'],points[13+offset]['y']])
            unit=(wrist-elbow)/np.linalg.norm(wrist-elbow);theta=math.radians(bend)
            palm=np.array([[math.cos(theta),-math.sin(theta)],[math.sin(theta),math.cos(theta)]])@unit
            center=wrist+palm*.05;across=np.array([-palm[1],palm[0]])*.018
            kp=[{'x':float(wrist[0]),'y':float(wrist[1]),'score':.9,'coordinate_valid':True} for _ in range(21)]
            for index,xy in ((5,center-across),(9,center),(13,center+across)):
                kp[index].update(x=float(xy[0]),y=float(xy[1]))
            hands.append({'body_wrist_index':wrist_index,'keypoints':kp})
        f['pretrained']={'status':'observed','timestamp_ms':stamp,'body':{'keypoints':body},'hands':hands,
                         'body_model_sha256':'synthetic-body-fixture','hand_model_sha256':'synthetic-hand-fixture'}
    return f


def selected(items,suffix,side=None):
    return [i for i in items if i['code'].endswith(suffix) and (side is None or i.get('side')==side)]


class MovementTests(unittest.TestCase):
    def test_lag_direction_normal_amplitude_difference_and_alternating(self):
        for delay,status in ((0,'not_observed'),(400,'candidate'),(-400,'candidate'),(1200,'unassessable')):
            rows=[{'stamp':t,'left':{'elbow':100+45*math.sin(t/2400*2*math.pi)},
                'right':{'elbow':100+30*math.sin((t-delay)/2400*2*math.pi)}} for t in range(0,4201,100)]
            result=lag_observation(rows,'elbow');self.assertEqual(result['status'],status,result)
            if status=='candidate':self.assertEqual(result['side'],'right' if delay>0 else 'left');self.assertAlmostEqual(result['lag_ms'],delay,delta=100)

    def test_phase_requires_motion_continuity_and_same_simultaneous_variant(self):
        self.assertNotIn('hammer_curl',SYNC_SPECS)
        observer=MovementGuidance('machine_chest_press','fixture')
        for t in range(0,6500,100):
            f=frame(t,100+45*math.sin(t/2400*2*math.pi),100+45*math.sin((t-400)/2400*2*math.pi))
            items,_=observer.update(f,{})
        issue=selected(items,'phase_lag')[0];self.assertEqual(issue['status'],'issue');self.assertEqual(issue['side'],'right')
        f=frame(6500);f['framing']['landmarks'][16]['valid']=False
        items,_=observer.update(f,{})
        self.assertEqual(selected(items,'phase_lag')[0]['status'],'unassessable')
        items,_=observer.update(frame(6600),{'movement_pattern':'alternating'})
        self.assertEqual(selected(items,'phase_lag')[0]['reason'],'simultaneous_variant_required')
        items,_=observer.update(frame(8000),{})
        self.assertNotEqual(selected(items,'phase_lag')[0]['status'],'issue')

    def test_static_pose_and_single_monotonic_movement_are_not_phase_errors(self):
        for static in (True,False):
            rows=[{'stamp':t,'left':{'elbow':100 if static else 50+t/100},'right':{'elbow':70 if static else 30+t/100}} for t in range(0,4201,100)]
            self.assertEqual(lag_observation(rows,'elbow')['status'],'unassessable')

    def test_return_phase_distinguishes_fast_controlled_and_curl_direction(self):
        for direction in (1,-1):
            for duration,fast in ((300,True),(1000,False)):
                tracker=ReturnPhase(direction);events=[];period=1600+duration
                for t in range(0,period*3,100):
                    x=t%period;value=60+100*x/1600 if x<=1600 else 160-100*(x-1600)/duration
                    event=tracker.update(t,value if direction==1 else 220-value)
                    if event:events.append(event)
                self.assertTrue(events);self.assertTrue(all(e['fast']==fast for e in events),events)
                self.assertTrue(all(e['end_ms']>e['start_ms'] for e in events))

    def test_final_return_settles_once_without_starting_another_repetition(self):
        for direction in (1,-1):
            for duration,fast in ((300,True),(1000,False)):
                tracker=ReturnPhase(direction);events=[]
                for stamp in range(0,4200,100):
                    value=60+100*stamp/1600 if stamp<=1600 else max(60,160-100*(stamp-1600)/duration)
                    event=tracker.update(stamp,value if direction==1 else 220-value)
                    if event:events.append((stamp,event))
                self.assertEqual(len(events),1,events)
                emitted,event=events[0]
                self.assertEqual(event['confirmation_basis'],'settled_return')
                self.assertEqual(event['fast'],fast)
                self.assertEqual(event['start_ms'],1600)
                self.assertEqual(event['end_ms'],1600+duration)
                self.assertLessEqual(emitted-event['end_ms'],400)
                self.assertGreaterEqual(event['return_observed_samples'],3)
                self.assertAlmostEqual(event['return_to_effort_speed_ratio'],
                    (event['return_excursion_degrees']/event['return_ms'])/
                    (event['effort_excursion_degrees']/event['effort_ms']))

    def test_partial_return_and_isolated_spike_do_not_form_a_complete_return(self):
        for mode in ('partial','spike','gap','nan'):
            tracker=ReturnPhase(1);events=[]
            for stamp in range(0,4000,100):
                value=60+100*stamp/1600 if stamp<=1600 else max(60,160-100*(stamp-1600)/300)
                if mode=='partial' and stamp>1600:value=max(120,160-40*(stamp-1600)/300)
                if mode=='spike':value=160 if stamp==1600 else 60
                if mode=='gap' and 1700<=stamp<=2200:continue
                if mode=='nan' and stamp==1700:value=float('nan')
                event=tracker.update(stamp,value)
                if event:events.append(event)
            self.assertEqual(events,[],mode)

    def test_comparable_shorter_return_uses_speed_instead_of_duration_ratio(self):
        tracker=ReturnPhase(1);events=[]
        for stamp in range(0,2000,50):
            value=60+100*stamp/800 if stamp<=800 else max(80,160-80*(stamp-800)/400)
            event=tracker.update(stamp,value)
            if event:events.append(event)
        self.assertEqual(len(events),1)
        event=events[0]
        self.assertLess(event['return_to_effort_ratio'],.55)
        self.assertAlmostEqual(event['return_to_effort_speed_ratio'],1.6)
        self.assertFalse(event['fast'])

    def test_partial_rebound_cannot_be_labeled_fast_from_duration_alone(self):
        tracker=ReturnPhase(1);events=[]
        for stamp in range(0,2400,100):
            if stamp<=1600:value=60+100*stamp/1600
            elif stamp<=1900:value=160-40*(stamp-1600)/300
            else:value=120+40*(stamp-1900)/400
            event=tracker.update(stamp,value)
            if event:events.append(event)
        self.assertEqual(events,[])

    def test_elbow_relative_drift_ignores_global_translation_and_scale(self):
        for drifting in (False,True):
            observer=MovementGuidance('standing_dumbbell_curl','fixture')
            for t in range(0,6500,100):
                wave=math.sin(t/1800*2*math.pi)
                items,_=observer.update(frame(t,100+45*wave,100+45*wave,drift=.12*wave if drifting else 0,
                                              translation=.03*wave,scale=1+.05*wave),{})
            result=selected(items,'elbow_drift','left')[0]
            self.assertEqual(result['status'],'issue' if drifting else 'not_observed',result)

    def test_wrist_bend_uses_actual_palm_and_withdraws_only_hidden_side(self):
        for bend,status in ((0,'not_observed'),(60,'issue')):
            observer=MovementGuidance('machine_chest_press','fixture')
            for t in range(0,1300,100):items,_=observer.update(frame(t,bend=bend),{})
            self.assertEqual(selected(items,'wrist_bend','left')[0]['status'],status)
            self.assertFalse(selected(items,'wrist_bend','left')[0]['evidence']['contact_with_handle_verified'])
            missing=frame(1300,bend=bend);missing['pretrained']['hands'][0]['keypoints'][9]['score']=.1
            items,_=observer.update(missing,{})
            self.assertEqual(selected(items,'wrist_bend','left')[0]['status'],'unassessable')
            self.assertEqual(selected(items,'wrist_bend','right')[0]['status'],status)

    def test_hand_association_stale_timestamp_and_missing_palm_refuse_wrist(self):
        f=frame(100,bend=60);self.assertEqual(wrist_observation(f,'left')['status'],'observed')
        f['pretrained']['timestamp_ms']=0;self.assertEqual(wrist_observation(f,'left')['status'],'unassessable')
        f=frame(100,bend=60);f['pretrained']['hands'][0]['keypoints'][0]['x']+=.2
        self.assertEqual(wrist_observation(f,'left')['status'],'unassessable')
        self.assertEqual(wrist_observation(frame(100),'left')['status'],'unassessable')

    def test_shifted_session_clock_keeps_same_frame_body_and_palm_evidence(self):
        observer=MovementGuidance('machine_chest_press','fixture')
        for t in range(0,1300,100):
            f=frame(t,bend=60);f.update(captured_ms=7000+t,source_captured_ms=t)
            self.assertEqual(frame_measurements(f)[1],'rtmpose_reference')
            items,_=observer.update(f,{})
        item=selected(items,'wrist_bend','left')[0]
        self.assertEqual(item['status'],'issue')
        self.assertGreaterEqual(item['start_ms'],7000);self.assertEqual(item['end_ms'],8200)
        f['pretrained']['timestamp_ms']=f['captured_ms']
        self.assertEqual(wrist_observation(f,'left')['status'],'unassessable')
        self.assertEqual(frame_measurements(f)[1],'mediapipe_original')

    def test_unsupported_view_restores_continuous_window_and_never_persists_old_cue(self):
        observer=MovementGuidance('standing_dumbbell_curl','fixture')
        for t in range(0,6000,100):
            wave=math.sin(t/1800*2*math.pi);items,_=observer.update(frame(t,100+45*wave,100+45*wave,drift=.12*wave),{})
        self.assertTrue(any(i['status']=='issue' for i in selected(items,'elbow_drift')))
        items,_=observer.update(frame(6000,view='front'),{})
        item=selected(items,'elbow_drift')[0];self.assertEqual(item['status'],'unassessable');self.assertNotIn('side',item)
        items,_=observer.update(frame(6100),{})
        self.assertFalse(any(i['status']=='issue' for i in selected(items,'elbow_drift')))

    def test_two_to_ten_fps_persistent_posture_needs_real_samples_and_withdraws_after_gap(self):
        for interval in (100,200,250,280,333,400,500):
            for drifting in (False,True):
                observer=MovementGuidance('standing_dumbbell_curl','fixture');issues=[]
                for stamp in range(0,8000,interval):
                    wave=math.sin(stamp/2400*2*math.pi)
                    items,_=observer.update(frame(stamp,100+45*wave,100+45*wave,drift=.12*wave if drifting else 0),{})
                    issues.extend(selected(items,'elbow_drift','left'))
                self.assertEqual(any(i['status']=='issue' for i in issues),drifting,(interval,drifting))
                for item in issues:
                    if item['status']=='issue':
                        self.assertGreaterEqual(item['evidence']['observed_frames'],4)
                        self.assertGreaterEqual(item['evidence']['observed_duration_ms'],1200)
                        self.assertLessEqual(item['evidence']['maximum_observation_gap_ms'],500)
            for bend in (0,60):
                observer=MovementGuidance('machine_chest_press','fixture')
                for stamp in range(0,3000,interval):
                    items,_=observer.update(frame(stamp,bend=bend),{})
                    if stamp<700:self.assertNotEqual(selected(items,'wrist_bend','left')[0]['status'],'issue')
                item=selected(items,'wrist_bend','left')[0]
                self.assertEqual(item['status'],'issue' if bend else 'not_observed',interval)
                self.assertGreaterEqual(item['evidence']['observed_frames'],4)
                items,_=observer.update(frame(stamp+600,bend=bend),{})
                self.assertEqual(selected(items,'wrist_bend','left')[0]['status'],'unassessable')
                self.assertEqual(selected(items,'wrist_bend','left')[0]['evidence']['observed_frames'],1)

    def test_sparse_posture_does_not_publish_phase_lag_or_fast_return(self):
        observer=MovementGuidance('machine_chest_press','fixture')
        for stamp in range(0,10000,400):
            items,_=observer.update(frame(stamp,100+45*math.sin(stamp/2400*2*math.pi),
                100+45*math.sin((stamp-400)/2400*2*math.pi),bend=60),{})
            self.assertFalse(any(i['status']=='issue' for i in selected(items,'phase_lag')+selected(items,'fast_return')))
        self.assertEqual(selected(items,'wrist_bend','left')[0]['status'],'issue')
        missing=frame(stamp+400,bend=60);missing['identity_accepted']=False
        items,_=observer.update(missing,{})
        self.assertFalse(any(i['status']=='issue' for i in items))
        items,_=observer.update(frame(stamp+800,bend=60),{})
        self.assertEqual(selected(items,'wrist_bend','left')[0]['status'],'unassessable')

    def test_default_and_enabled_capabilities_are_separate(self):
        try:
            with patch.dict(os.environ,{'FITNESS_MOVEMENT_GUIDANCE':'0'}):
                policy_version.cache_clear();old=policy_version();self.assertFalse(any(r['code'] in RULES for r in rules_for('machine_chest_press')))
            with patch.dict(os.environ,{'FITNESS_MOVEMENT_GUIDANCE':'1'}):
                policy_version.cache_clear();self.assertNotEqual(old,policy_version());self.assertEqual(sum(r['code'] in RULES for r in rules_for('machine_chest_press')),3)
        finally:policy_version.cache_clear()


class MovementBackendTests(unittest.TestCase):
    def setUp(self):
        import test_formal_records as fixtures
        self.env=patch.dict(os.environ,{'FITNESS_MOVEMENT_GUIDANCE':'1','FITNESS_PRETRAINED_CORRECTION':'0','FITNESS_BODY_LEAN':'0'});self.env.start();policy_version.cache_clear()
        self.fx=fixtures.RecordTests();self.fx.setUp()
    def tearDown(self):
        try:
            for hook in self.fx.app.extensions.get('stop_hooks',[]):hook()
            self.fx.tearDown()
        finally:self.env.stop();policy_version.cache_clear()

    def test_real_recovered_palm_cache_saves_right_wrist_issue_and_highlight(self):
        import json
        from fitness_app.paths import ROOT
        from fitness_app.posture_engine import create_analysis_engine
        from fitness_app.coaching import committed_issues,coaching_snapshot,coaching_observation
        from fitness_app.skeleton import skeleton_snapshot
        cache=json.loads((ROOT/'data/verification/round3/movement-guidance-review.json').read_text(encoding='utf-8'))
        original=next(v for v in cache['videos'] if v['number']=='1694')
        recovered=json.loads((ROOT/'data/verification/round3/hand-recovery-review.json').read_text(encoding='utf-8'))
        review=next(v for v in recovered['videos'] if v['number']=='1694')
        self.assertEqual(review['video_sha256'],original['source_sha256'])
        by={row['t']:row for row in original['frames']};engine=create_analysis_engine('machine_chest_press')
        sid=self.fx.running('machine_chest_press');highlights=[]
        for row in review['rows']:
            stamp=row['t'];base=by[stamp]
            current={'captured_ms':stamp,'frame_seq':stamp,'width':1280,'height':720,
                'identity_accepted':base['identity_accepted'],'framing':{'person_count':base['base_people'],
                'code':base['base_framing'],'usable':base['identity_accepted'],'landmarks':base['original_landmarks']},
                'pretrained':{'status':'observed' if base.get('body') else 'unassessable','timestamp_ms':stamp,
                'body':base.get('body'),'hands':row['hands']}}
            output=engine.process(current,{'coaching_view':'auto','stream_epoch':'real-recovered-cache'})
            if output.get('posture_findings'):
                saved=self.fx.writer.submit(self.fx.user,sid,self.fx.auth,'posture',
                    {'set_index':1,'findings':output['posture_findings'],'active_ms':stamp})
                self.assertEqual(saved['save_state'],'clean')
            issues=[i for i in committed_issues(output) if i['code'].endswith('wrist_bend')]
            if issues:
                current['analysis']=coaching_observation(output)
                cue=coaching_snapshot({'status':'running','action_id':'machine_chest_press','supported_items':engine.rules},current,issues)
                highlights.append(skeleton_snapshot(current,cue)['highlight'])
        record=self.fx.store.snapshot(self.fx.user,sid,True)
        issues=[i for i in record['posture_observations'] if i['code'].endswith('wrist_bend') and i['status']=='issue']
        self.assertTrue(issues);self.assertEqual({i['side'] for i in issues},{'right'})
        self.assertTrue(any(highlights));self.assertEqual(record['total_count'],0);self.fx.finish(sid)

    def test_final_repetition_issue_is_saved_before_next_effort_without_adding_count(self):
        from fitness_app.posture_engine import create_analysis_engine
        from fitness_app.coaching import committed_issues
        engine=create_analysis_engine('machine_chest_press');sid=self.fx.running('machine_chest_press');seen=[]
        for stamp in range(0,3300,100):
            value=60+100*stamp/1600 if stamp<=1600 else max(60,160-100*(stamp-1600)/300)
            output=engine.process(frame(stamp,value,value),{'coaching_view':'auto','stream_epoch':'last-return-fixture'})
            seen.extend(i for i in committed_issues(output) if i['code'].endswith('fast_return'))
            if output.get('posture_findings'):
                result=self.fx.writer.submit(self.fx.user,sid,self.fx.auth,'posture',
                    {'set_index':1,'findings':output['posture_findings'],'active_ms':stamp})
                self.assertEqual(result['save_state'],'clean')
        self.assertTrue(seen)
        record=self.fx.store.snapshot(self.fx.user,sid,True)
        issues=[i for i in record['posture_observations'] if i['status']=='issue' and i['code'].endswith('fast_return')]
        self.assertEqual({i['side'] for i in issues},{'left','right'})
        self.assertTrue(all(i['evidence']['confirmation_basis']=='settled_return' for i in issues))
        self.assertTrue(all((i['start_ms'],i['end_ms'])==(1600,1900) for i in issues))
        self.assertEqual(record['total_count'],0);self.fx.finish(sid)

    def test_sparse_real_engine_persists_wrist_error_and_withdraws_after_missing_palm(self):
        from fitness_app.posture_engine import create_analysis_engine
        from fitness_app.coaching import coaching_snapshot,committed_issues
        from fitness_app.skeleton import skeleton_snapshot
        for interval in (280,333,400,500):
            engine=create_analysis_engine('machine_chest_press');sid=self.fx.running('machine_chest_press')
            for stamp in range(0,3500,interval):
                current=frame(stamp,bend=60);current['frame_seq']=stamp
                output=engine.process(current,{'coaching_view':'auto','stream_epoch':'sparse-fixture'})
                if output.get('posture_findings'):
                    saved=self.fx.writer.submit(self.fx.user,sid,self.fx.auth,'posture',
                        {'set_index':1,'findings':output['posture_findings'],'active_ms':stamp})
                    self.assertEqual(saved['save_state'],'clean')
            record=self.fx.store.snapshot(self.fx.user,sid,True)
            errors=[i for i in record['posture_observations'] if i['code'].endswith('wrist_bend') and i['status']=='issue']
            self.assertEqual({i['side'] for i in errors},{'left','right'})
            self.assertTrue(all(i['evidence']['observed_frames']>=4 for i in errors))
            feedback=committed_issues(output);current['analysis']=output['observation']
            session={'status':'running','action_id':'machine_chest_press','supported_items':engine.rules}
            cue=coaching_snapshot(session,current,feedback)
            self.assertIsNotNone(cue['primary_cue']);self.assertTrue(skeleton_snapshot(current,cue)['highlight'])
            missing=frame(stamp+interval,bend=60);missing['frame_seq']=stamp+interval
            for hand in missing['pretrained']['hands']:hand['keypoints'][9]['score']=.1
            missing['analysis']=engine.process(missing,{'coaching_view':'auto','stream_epoch':'sparse-fixture'})['observation']
            cue=coaching_snapshot(session,missing,feedback,interval)
            self.assertIsNone(cue['primary_cue']);self.assertFalse(skeleton_snapshot(missing,cue)['highlight'])
            self.fx.finish(sid)

    def test_real_engine_save_guidance_cue_highlight_and_occlusion_retraction(self):
        from fitness_app.posture_engine import create_analysis_engine
        from fitness_app.coaching import coaching_snapshot,committed_issues
        from fitness_app.skeleton import skeleton_snapshot
        engine=create_analysis_engine('machine_chest_press');sid=self.fx.running('machine_chest_press')
        for t in range(0,6500,100):
            f=frame(t,100+45*math.sin(t/2400*2*math.pi),100+45*math.sin((t-400)/2400*2*math.pi))
            result=engine.process(f,{'coaching_view':'auto','stream_epoch':'guidance-fixture'})
            if result.get('posture_findings'):
                state=self.fx.writer.submit(self.fx.user,sid,self.fx.auth,'posture',{'set_index':1,'findings':result['posture_findings'],'active_ms':t})
                self.assertEqual(state['save_state'],'clean')
        stored=self.fx.store.snapshot(self.fx.user,sid,True)
        self.assertTrue(any(i['code'].endswith('phase_lag') and i['status']=='issue' and i['side']=='right' for i in stored['posture_observations']))
        self.assertEqual(stored['total_count'],0)
        feedback=[i for i in committed_issues(result) if i['code'].endswith('phase_lag')]
        f['analysis']=result['observation'];session={'status':'running','action_id':'machine_chest_press','supported_items':engine.rules}
        cue=coaching_snapshot(session,f,feedback);self.assertEqual(cue['state'],'needs_adjustment');self.assertTrue(skeleton_snapshot(f,cue)['highlight'])
        f=frame(6500);f['framing']['landmarks'][16]['valid']=False
        f['analysis']=engine.process(f,{'coaching_view':'auto','stream_epoch':'guidance-fixture'})['observation']
        cue=coaching_snapshot(session,f,feedback,100);self.assertIsNone(cue['primary_cue']);self.assertFalse(skeleton_snapshot(f,cue)['highlight'])


    def test_all_four_types_can_be_saved_with_expected_side_and_highlight(self):
        from fitness_app.posture_engine import create_analysis_engine
        from fitness_app.coaching import coaching_snapshot,committed_issues
        from fitness_app.skeleton import skeleton_snapshot
        cases=[('machine_chest_press','wrist_bend'),('standing_dumbbell_curl','elbow_drift'),('machine_chest_press','fast_return')]
        for action,suffix in cases:
            with self.subTest(suffix=suffix):
                engine=create_analysis_engine(action);sid=self.fx.running(action);observed=[];highlight=False
                for t in range(0,6600,100):
                    if suffix=='wrist_bend':f=frame(t,bend=60)
                    elif suffix=='elbow_drift':
                        wave=math.sin(t/1800*2*math.pi);f=frame(t,100+45*wave,100+45*wave,drift=.12*wave)
                    else:
                        phase=t%1900;v=60+100*phase/1600 if phase<=1600 else 160-100*(phase-1600)/300
                        f=frame(t,v,v)
                    result=engine.process(f,{'coaching_view':'auto','stream_epoch':'all-types-'+suffix})
                    if result.get('posture_findings'):
                        state=self.fx.writer.submit(self.fx.user,sid,self.fx.auth,'posture',{'set_index':1,'findings':result['posture_findings'],'active_ms':t})
                        self.assertEqual(state['save_state'],'clean')
                    issues=[i for i in committed_issues(result) if i['code'].endswith(suffix)]
                    observed.extend(issues)
                    if issues:
                        f['analysis']=result['observation'];cue=coaching_snapshot({'status':'running','action_id':action,'supported_items':engine.rules},f,issues)
                        highlight=highlight or bool(skeleton_snapshot(f,cue)['highlight'])
                self.assertTrue(observed);self.assertTrue(highlight)
                saved=self.fx.store.snapshot(self.fx.user,sid,True)
                self.assertTrue(any(i['status']=='issue' and i['code'].endswith(suffix) for i in saved['posture_observations']))
                self.assertEqual(saved['total_count'],0);self.fx.finish(sid)



if __name__=='__main__':unittest.main()
