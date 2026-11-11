"""本轮真实杠铃视频与针对性修正；缓存接线断言不冒充独立质量真值。"""
import copy
import hashlib
import io
import json
from pathlib import Path
import sys
import unittest

import numpy as np
from fitness_app import db_schema as s
from fitness_app.catalogue import get_action
from fitness_app.frame_pipeline import framing,analysis_framing
from fitness_app.posture_engine import create_analysis_engine,PostureEngine
from fitness_app.posture_rules import rules_for,report_findings
from fitness_app.barbell_quality import evaluate_row_trunk
from fitness_app.paths import ROOT
from test_barbell_row import geometry as row_frame
from test_barbell_quality import press,cycle
import test_formal_records as record_fixture
sys.path.insert(0,str(ROOT/'scripts'))


class RowRefinementTests(unittest.TestCase):
    def evaluate(self,frames):
        return evaluate_row_trunk({'start_ms':frames[0]['captured_ms'],'end_ms':frames[-1]['captured_ms']},frames)

    def test_single_frame_spike_is_not_a_sustained_trunk_change(self):
        frames=[row_frame(0,i,sway=.2 if i==12 else 0) for i in range(25)]
        result=self.evaluate(frames)
        self.assertEqual(result['status'],'not_observed',result)
        self.assertGreater(result['raw_range_by_side']['left'],15)
        self.assertLess(result['trunk_change_degrees'],15)

    def test_sustained_change_and_conflicting_sides_are_distinguished(self):
        frames=[row_frame(0,i,sway=.2 if 8<=i<=16 else 0) for i in range(25)]
        self.assertEqual(self.evaluate(frames)['status'],'issue')
        for frame in frames:
            normal=row_frame(0,frame['frame_seq'])
            for index in (12,24):frame['framing']['landmarks'][index]=normal['framing']['landmarks'][index]
        result=self.evaluate(frames)
        self.assertEqual(result['status'],'unassessable')
        self.assertEqual(result['reason'],'bilateral_projection_disagreement')

    def test_temporal_gap_and_degenerate_projection_refuse(self):
        frames=[row_frame(0,i) for i in range(8)]
        for f in frames[4:]:f['captured_ms']+=600
        self.assertEqual(self.evaluate(frames)['reason'],'cycle_evidence_gap')
        frames=[row_frame(0,i) for i in range(8)]
        for f in frames:
            for a,b in ((11,23),(12,24)):f['framing']['landmarks'][a]=copy.deepcopy(f['framing']['landmarks'][b])
        self.assertEqual(self.evaluate(frames)['status'],'unassessable')


class ShoulderRefinementTests(unittest.TestCase):
    def test_partial_view_allows_only_visible_upper_body_and_preserves_counting_gate(self):
        raw=press()['framing']['landmarks']
        for i in (25,26,27,28,31,32):raw[i]['visibility']=0
        result={'poses':[raw]}
        self.assertFalse(framing('barbell_overhead_press',result)['usable'])
        local=analysis_framing('barbell_overhead_press',result)
        self.assertTrue(local['usable']);self.assertFalse(local['counting_usable'])
        self.assertEqual(local['posture_scope'],'upper_body')
        self.assertFalse(create_analysis_engine('barbell_overhead_press').counting_available)
        raw[15]['visibility']=0
        self.assertFalse(analysis_framing('barbell_overhead_press',result)['usable'])
        self.assertFalse(analysis_framing('barbell_overhead_press',{'poses':[raw,raw]})['usable'])

    def test_new_arm_or_direction_must_confirm_its_own_duration(self):
        engine=create_analysis_engine('barbell_overhead_press')
        for i in range(18):
            frame=press(.12);normal=press()
            frame['framing']['landmarks'][14 if i<9 else 13]=normal['framing']['landmarks'][14 if i<9 else 13]
            frame.update(frame_seq=i,captured_ms=i*100)
            out=engine.process(frame,{'coaching_view':'front','stream_epoch':'left-right-change'})
            item=out['observation']['posture']['items'][0]
            if i==8:
                self.assertEqual(item['status'],'issue');self.assertIn('左肘',item['message'])
                self.assertEqual(item['evidence']['arm_deviation']['left']['direction'],'outward')
            if i==9:self.assertEqual(item['status'],'unassessable')
        self.assertEqual(item['status'],'issue');self.assertIn('右肘',item['message'])


class PostureReportRefinementTests(unittest.TestCase):
    def test_no_cycles_explicitly_reports_insufficient_evidence_and_unsupported_back_error(self):
        report=report_findings({'supported_items':rules_for('conventional_deadlift'),'events':[]})
        self.assertEqual(report['assessment_state'],'evidence_insufficient')
        self.assertEqual(report['item_summary'][0]['status'],'unassessable')
        self.assertIn('脊柱局部弯曲',''.join(report['limitations']))

    def test_deadlift_suggestion_names_hip_when_only_hip_is_short(self):
        frames=cycle(145,175);engine=PostureEngine('conventional_deadlift',None);engine.frames.extend(frames)
        item=engine._cycle({'start_ms':0,'end_ms':frames[-1]['captured_ms'],'side':'none'},'side','user_setting')[0]
        self.assertEqual(item['status'],'issue');self.assertEqual(item['evidence']['limited_joints'],['髋'])
        self.assertIn('髋仍有屈曲',item['message']);self.assertNotIn('膝仍有屈曲',item['message'])
        self.assertEqual(item['evidence']['minimum_hip_degrees'],160)


class RealVideoPostureTests(unittest.TestCase):
    def setUp(self):
        self.fx=record_fixture.RecordTests();self.fx.setUp()
    def tearDown(self):
        for name in ('video_jobs','session_manager','preview_manager','media_inputs'):self.fx.app.extensions[name].stop()
        self.fx.tearDown()

    def current_case(self,key):
        from fitness_app.pose_input import input_policy_snapshot,ContentPoseService
        report=json.loads((ROOT/'data/verification/step-19/barbell-posture-review.json').read_text(encoding='utf-8'))['current_input_recheck']
        self.assertEqual(report['input_policy'],input_policy_snapshot(ContentPoseService),'先运行明确授权的输入策略复查，不能静默重推旧视频')
        case=report['cases'][key]
        self.assertTrue((ROOT/case['pose_cache']).is_file())
        with np.load(ROOT/case['pose_cache'],allow_pickle=False) as archive:
            meta=json.loads(str(archive['metadata']))
        self.assertEqual(meta['policy'],report['input_policy'])
        self.assertEqual(meta['source_sha256'],case['source_sha256'])
        self.assertEqual(hashlib.sha256((ROOT/case['path']).read_bytes()).hexdigest(),case['source_sha256'])
        return case

    def run_video(self,action,path,cache,view,payload=None):
        # 解码真实原片，消费已取得的真实Full关键点；不重复执行模型。
        with np.load(ROOT/cache,allow_pickle=False) as archive:
            stamps={int(t):i for i,t in enumerate(archive['timestamps_ms'])}
            points=archive['points'].copy();counts=archive['person_counts'].copy()
        class CachedRealPose:
            def __init__(self,**unused):pass
            def detect(self,rgb,stamp):
                i=stamps[stamp];people=int(counts[i])
                raw=[dict(zip(('x','y','z','visibility','presence'),map(float,p))) for p in points[i]]
                return {'poses':[raw] if people==1 else [[] for _ in range(people)],'model':'cached-real-mediapipe-full'}
            def close(self):pass
        fx=self.fx;media=fx.app.extensions['media_inputs'];jobs=fx.app.extensions['video_jobs'];jobs.pose_factory=CachedRealPose
        with (io.BytesIO(payload) if payload is not None else (ROOT/path).open('rb')) as stream:uploaded=media.upload(fx.user,fx.auth,stream,s.new_id())
        iid=uploaded['input_id'];media.threads[iid].join(10)
        self.assertEqual(media.snapshot(fx.user,iid)['status'],'ready')
        created=fx.post(fx.client,'/api/v1/sessions',{'action_id':action,'capability_version':get_action(action)['capability_version'],
                       'source':'video','mode':'free','target':None,'coaching_view':view,'input_id':iid})
        self.assertEqual(created.status_code,201,created.json);sid=created.json['data']['session_id']
        started=fx.post(fx.client,'/api/v1/jobs/video',{'session_id':sid,'expected_version':0})
        self.assertEqual(started.status_code,202,started.json);jid=started.json['data']['job_id']
        jobs.runs[jid].thread.join(30);self.assertFalse(jobs.runs[jid].thread.is_alive())
        job=jobs.snapshot(fx.user,jid);self.assertEqual(job['status'],'completed',job)
        report=fx.client.get('/api/v1/records/'+sid+'/posture').json['data']
        self.assertFalse(media._path(iid).exists());self.assertTrue((ROOT/path).exists())
        return job,report

    def test_real_frontal_press_partial_body_persists_local_assessments(self):
        case=self.current_case('press')
        job,report=self.run_video('barbell_overhead_press',case['path'],case['pose_cache'],'front')
        self.assertEqual(job['processed_frames'],513);self.assertFalse(job['counting_available']);self.assertIsNone(report['count'])
        self.assertGreater(report['item_summary'][0]['assessed_observations'],0)
        self.assertEqual(report['issue_count'],0)
        self.assertTrue(all('不验证全身站姿' in f['evidence']['scope'] for f in report['findings']))

    def test_real_row_unchanged_cycles_and_projection_disagreement_are_saved(self):
        case=self.current_case('row')
        job,report=self.run_video('barbell_bent_over_row',case['path'],case['pose_cache'],'oblique')
        self.assertEqual(job['session']['total_count'],3);self.assertEqual(report['issue_count'],0)
        self.assertEqual(len(report['findings']),3)
        unknown=[f for f in report['findings'] if f['status']=='unassessable']
        self.assertEqual(len(unknown),1);self.assertIn('严格侧面',unknown[0]['suggestion'])
        self.assertIn('range_by_side',unknown[0]['evidence'])

    def test_real_pb_lift_does_not_turn_source_label_into_extension_error(self):
        case=self.current_case('deadlift_current_input')
        job,report=self.run_video('conventional_deadlift',case['path'],case['pose_cache'],'side')
        self.assertEqual(job['session']['total_count'],1);self.assertEqual(report['issue_count'],0)
        self.assertEqual(report['findings'][0]['status'],'not_observed')
        self.assertIn('PB',''.join(report['limitations']))
        self.assertEqual(report['assessment_state'],'no_issue_observed_in_assessed_items')

    def test_real_press_frames_realtime_manager_keeps_partial_scope_and_zero_counts(self):
        from validate_real_posture import current_pose
        case=self.current_case('press')
        frames,meta=current_pose(ROOT/case['path'],'barbell_overhead_press',Path(case['pose_cache']).name)
        self.assertTrue(meta['cache_hit']);self.assertEqual(meta['new_inference_frames'],0)
        fx=self.fx;manager=fx.app.extensions['session_manager'];action='barbell_overhead_press'
        created=fx.post(fx.client,'/api/v1/sessions',{'action_id':action,'capability_version':get_action(action)['capability_version'],
                       'source':'camera','mode':'free','target':None,'coaching_view':'front'})
        sid=created.json['data']['session_id']
        manager.claim(fx.user,fx.auth,fx.now[0]+3600000,sid,s.new_id(),{'page_id':s.new_id(),'takeover':False,'expected_version':0})
        rt=manager.live[sid];rt.data=fx.store.set_state(fx.user,sid,'running',s.new_id());rt.adapter=manager.engine_provider(action);rt.session_origin_mono=0
        for f in frames:
            fx.mono[0]=f['captured_ms']/1000;f['framing']['ready_for_tracking']=f['framing']['usable'];manager._consume(rt,f)
        report=fx.client.get('/api/v1/records/'+sid+'/posture').json['data']
        self.assertIsNone(report['count']);self.assertGreater(report['item_summary'][0]['assessed_observations'],0)
        self.assertEqual(fx.store.snapshot(fx.user,sid)['total_count'],0)
        self.assertFalse(manager.decorate_snapshot(fx.user,rt.data)['counting_available'])


if __name__=='__main__':unittest.main()
