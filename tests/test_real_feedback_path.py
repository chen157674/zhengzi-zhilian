"""固定开发视频的参考提示接实时／视频保存；缓存不充当独立正确性真值。"""
import hashlib
import io
import json
import time
import unittest

from fitness_app import db_schema as s
from fitness_app.catalogue import get_action
from fitness_app.paths import ROOT
from fitness_app.research_data import load_dataset
from diagnose_cycles import cached_frames
import test_real_barbell_posture as barbell
import test_formal_records as shared

RESULTS=[]
CASES={
    'squat':('bodyweight_squat','seven-exercises/squat/good/subject_005_squat_good_side.mp4'),
    'push_up':('standard_push_up','seven-exercises/push_up/bad/subject_013_push_up_bad_side.mp4'),
}


class RealFeedbackPathTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        samples={sample.path:sample for sample in load_dataset()}
        cls.inputs={}
        baseline=json.loads((ROOT/'data/experiments/baseline.json').read_text(encoding='utf-8'))
        for name,(action,path) in CASES.items():
            sample=samples[path]
            if sample.partition!='train' or 'side' not in sample.views:raise ValueError('只消费预定侧面训练片段，不使用test或临时换视角')
            if hashlib.sha256(sample.absolute_path.read_bytes()).hexdigest()!=sample.sha256:raise ValueError('原片哈希不符')
            entry=baseline['extractions'][sample.key]
            feature=json.loads((ROOT/'data/experiments/features'/f"{entry['feature_key']}.json").read_text(encoding='utf-8'))
            cache=ROOT/feature['pose_cache']
            cls.inputs[name]={'sample':sample,'frames':cached_frames(sample,baseline),'cache':cache,
                             'identity':{'source':sample.absolute_path.relative_to(ROOT).as_posix(),'source_sha256':sample.sha256,
                                         'partition':sample.partition,'source_label_not_error_truth':sample.label,
                                         'pose_cache':cache.relative_to(ROOT).as_posix(),'pose_sha256':hashlib.sha256(cache.read_bytes()).hexdigest()}}

    def setUp(self):self.fx=shared.RecordTests();self.fx.setUp()

    def tearDown(self):
        for name in ('video_jobs','session_manager','preview_manager','media_inputs'):self.fx.app.extensions[name].stop()
        self.fx.tearDown()

    def live_check(self,name):
        case=self.inputs[name];action=CASES[name][0];fx=self.fx;manager=fx.app.extensions['session_manager']
        created=fx.post(fx.client,'/api/v1/sessions',{'action_id':action,'capability_version':get_action(action)['capability_version'],
                       'source':'camera','mode':'free','target':None,'coaching_view':'side'})
        self.assertEqual(created.status_code,201,created.json);sid=created.json['data']['session_id']
        manager.claim(fx.user,fx.auth,fx.now[0]+3600000,sid,s.new_id(),{'page_id':s.new_id(),'takeover':False,'expected_version':0})
        rt=manager.live[sid];rt.data=fx.store.set_state(fx.user,sid,'running',s.new_id())
        rt.adapter=manager.engine_provider(action)
        rt.session_origin_mono=0;rt.stream_time_offset_ms=0
        samples=[];last_seq=0
        for source_frame in case['frames']:
            frame={**source_frame,'framing':dict(source_frame['framing'])};fx.mono[0]=frame['captured_ms']/1000
            frame['framing']['ready_for_tracking']=frame['framing']['usable']
            began=time.perf_counter();manager._consume(rt,frame)
            if rt.data['last_event_seq']==last_seq:continue
            last_seq=rt.data['last_event_seq']
            response=fx.client.get('/api/v1/sessions/'+sid);snapshot=response.json['data']
            elapsed=(time.perf_counter()-began)*1000
            self.assertEqual(response.status_code,200);self.assertEqual(snapshot['save_state'],'clean')
            if snapshot['coaching']['state']!='needs_adjustment':continue
            event=fx.store.snapshot(fx.user,sid,include_details=True)['events'][-1]
            cue=snapshot['coaching']['primary_cue'];issues=[i for i in event['assessments'] if i['status']=='issue']
            self.assertIn(cue['code'],[i['code'] for i in issues]);self.assertTrue(cue['message']);self.assertTrue(cue['suggestion'])
            self.assertTrue(cue['body_parts']);self.assertEqual(cue['evidence_level'],'reference')
            self.assertLessEqual(event['start_ms'],cue['start_ms']);self.assertLess(cue['start_ms'],cue['end_ms'])
            self.assertLessEqual(cue['end_ms'],event['end_ms']);self.assertIsNone(snapshot['coaching']['overall_correctness'])
            samples.append({'event_seq':event['event_seq'],'code':cue['code'],'side':cue['side'],
                            'message':cue['message'],'suggestion':cue['suggestion'],'body_parts':cue['body_parts'],
                            'start_ms':cue['start_ms'],'end_ms':cue['end_ms'],
                            'closing_frame_to_http_snapshot_ms':elapsed,
                            'counter_end_to_closing_frame_source_ms':frame['captured_ms']-event['end_ms']})
        self.assertGreater(len(samples),0,'固定真实输入未产生可接通的参考提示，不能用空结果通过')
        report=fx.client.get('/api/v1/records/'+sid+'/posture').json['data']
        self.assertEqual(report['time_base'],'session_elapsed_ms');self.assertEqual(report['assessment_state'],'issue_observed')
        self.assertTrue(report['no_issue_is_not_overall_correct'])
        RESULTS.append({**case['identity'],'mode':'realtime_cached_frames','frames':len(case['frames']),
                        'saved_count':rt.data['total_count'],'report_issue_observations':report['issue_count'],
                        'time_base':report['time_base'],'positive_guidance_samples':samples,
                        'new_inference_frames':0,'physical_camera':False,'accuracy':None})

    def video_check(self,name):
        import av
        case=self.inputs[name];helper=barbell.RealVideoPostureTests();helper.fx=self.fx
        # 原研究片为HEVC；不放宽用户上传门槛，内存编码合规H.264夹具并逐帧核源时间。
        clip=io.BytesIO();stamps=[f['captured_ms'] for f in case['frames']]
        with av.open(str(case['sample'].absolute_path)) as original,av.open(clip,'w',format='mp4') as target:
            source=original.streams.video[0];rate=source.average_rate
            encoded=target.add_stream('libx264',rate=rate);encoded.width=source.width;encoded.height=source.height
            encoded.pix_fmt='yuv420p';encoded.options={'preset':'ultrafast','crf':'23'}
            index=0
            for index,frame in enumerate(original.decode(video=0)):
                self.assertEqual(frame.rotation,0);self.assertEqual(round(float(frame.pts*frame.time_base)*1000),stamps[index])
                self.assertEqual(round(index*1000/rate),stamps[index])
                frame=frame.reformat(format='yuv420p');frame.pts=index;frame.time_base=1/rate
                for packet in encoded.encode(frame):target.mux(packet)
            self.assertEqual(index+1,len(stamps))
            for packet in encoded.encode():target.mux(packet)
        payload=clip.getvalue();clip.close()
        job,report=helper.run_video(CASES[name][0],case['identity']['source'],case['identity']['pose_cache'],'side',payload)
        issues=[f for f in report['findings'] if f['status']=='issue']
        self.assertGreater(len(issues),0);self.assertEqual(report['assessment_state'],'issue_observed')
        self.assertEqual(report['time_base'],'source_ms');self.assertEqual(job['coaching']['state'],'ended')
        for item in issues:
            self.assertTrue(item['message']);self.assertTrue(item['suggestion']);self.assertTrue(item['body_parts'])
            self.assertGreaterEqual(item['start_ms'],0);self.assertLess(item['start_ms'],item['end_ms'])
            self.assertLessEqual(item['end_ms'],job['processed_ms']);self.assertEqual(item['evidence_level'],'reference')
        RESULTS.append({**case['identity'],'mode':'video_decoded_with_cached_real_pose','frames':job['processed_frames'],
                        'saved_count':job['session']['total_count'],'report_issue_observations':len(issues),
                        'time_base':report['time_base'],'positive_guidance_samples':issues,
                        'normalized_test_input':{'codec':'h264','pixel_format':'yuv420p','source_codec':'hevc','geometry_unchanged':True,
                                                 'source_timestamps_checked':len(stamps),'bytes':len(payload),'sha256':hashlib.sha256(payload).hexdigest()},
                        'new_inference_frames':0,'physical_camera':False,'accuracy':None})

    def test_real_squat_reference_cue_reaches_live_http_and_saved_timeline(self):self.live_check('squat')
    def test_real_pushup_reference_cue_reaches_live_http_and_saved_timeline(self):self.live_check('push_up')
    def test_real_squat_video_keeps_reference_issue_times_after_cleanup(self):self.video_check('squat')
    def test_real_pushup_video_keeps_reference_issue_times_after_cleanup(self):self.video_check('push_up')


if __name__=='__main__':unittest.main()
