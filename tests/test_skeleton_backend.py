"""只验证新增骨架输出、归属和生命周期，不重跑原算法准确性矩阵。"""
import copy
import math
import threading
import unittest
from fitness_app.skeleton import skeleton_snapshot,REGIONS
from fitness_app.posture_rules import RULES
from fitness_app.frame_pipeline import framing
from fitness_app import db_schema as s
from fitness_app.auth import APIError
import test_coaching_backend as coaching
import test_formal_video_jobs as videos
import test_formal_preview as preview


class SkeletonTests(unittest.TestCase):
    def frame(self):
        return {'frame_seq':9,'captured_ms':600,'width':640,'height':480,
                'framing':framing('bodyweight_squat',videos.SyntheticPose().detect(None,600))}

    def test_visible_edges_and_anatomical_side_highlight(self):
        frame=self.frame();original=copy.deepcopy(frame)
        cue={'state':'needs_adjustment','primary_cue':{'code':'overhead_press.elbow','side':'left','cue_id':'saved',
             'start_ms':10,'end_ms':500}}
        value=skeleton_snapshot(frame,cue)
        self.assertTrue(value['available']);self.assertEqual(value['frame_seq'],9)
        self.assertFalse(value['mirrored']);self.assertEqual(value['highlight']['joint_indices'],[13,15])
        self.assertEqual(value['highlight']['connections'],[[13,15]])
        self.assertEqual(frame,original)
        from fitness_app.pretrained_policy import RULES as pretrained
        from fitness_app.movement_guidance_policy import RULES as guidance
        self.assertEqual(set(REGIONS),set(RULES)|set(pretrained)|set(guidance))

    def test_same_frame_leg_refinement_omits_unreliable_chain_and_keeps_primary_input(self):
        frame=self.frame();points=frame['framing']['landmarks']
        body=[{'coordinate_valid':False,'score':0.} for _ in range(17)]
        for source,target in ((11,23),(12,24)):
            body[source]={'x':points[target]['x'],'y':points[target]['y'],'coordinate_valid':True,'score':.9}
        for index,xy,score in [(13,(.3,.7),.9),(15,(.32,.9),.9),(14,(.7,.7),.2),(16,(.68,.9),.9)]:
            body[index]={'x':xy[0],'y':xy[1],'score':score,'coordinate_valid':True}
        frame['pretrained']={'status':'observed','timestamp_ms':600,'body':{'keypoints':body,'model_sha256':'1'*64}}
        original=copy.deepcopy(frame);value=skeleton_snapshot(frame);joints={p['index']:p for p in value['joints']}
        self.assertEqual((joints[25]['x'],joints[25]['y']),(.3,.7))
        self.assertFalse({26,28,29,30,31,32}&joints.keys())
        self.assertIn('rtmpose2d-lower-v1',value['model_version'])
        self.assertEqual(frame,original)
        from fitness_app.skeleton_store import pack_frame
        shifted=copy.deepcopy(frame);shifted['source_captured_ms']=600;shifted['captured_ms']=7600
        packed=pack_frame(shifted)
        self.assertEqual(packed['t'],7600);self.assertEqual(packed['source_ms'],600)
        self.assertEqual(packed['model'],value['model_version'])
        self.assertEqual({p[0] for p in packed['j']},set(joints))
        frame['pretrained']['timestamp_ms']=599
        stale=skeleton_snapshot(frame)
        self.assertFalse({25,26,27,28,29,30,31,32}&{p['index'] for p in stale['joints']})
        self.assertEqual(stale['joints'],[p for p in skeleton_snapshot(self.frame())['joints'] if p['index']<25])
        frame['pretrained']={'status':'unassessable','timestamp_ms':600}
        self.assertEqual(skeleton_snapshot(frame)['joints'],stale['joints'])

    def test_no_fabricated_connections_stale_identity_or_invalid_coordinates(self):
        frame=self.frame()
        frame['framing']['landmarks'][13]={'valid':False}
        frame['framing']['landmarks'][23]['x']=math.nan
        value=skeleton_snapshot(frame)
        self.assertNotIn(13,{j['index'] for j in value['joints']});self.assertNotIn(23,{j['index'] for j in value['joints']})
        self.assertFalse(any(13 in edge or 23 in edge for edge in value['connections']))
        self.assertEqual(skeleton_snapshot(frame,fresh=False)['joints'],[])
        frame['framing']['code']='TARGET_AMBIGUOUS'
        self.assertEqual(skeleton_snapshot(frame)['joints'],[])

    def test_nonissue_never_highlights_and_video_time_is_explicit(self):
        frame=self.frame()
        value=skeleton_snapshot(frame,{'state':'observing','primary_cue':{'code':'squat.range'}},time_base='source_ms')
        self.assertIsNone(value['highlight']);self.assertEqual(value['time_base'],'source_ms')
        self.assertFalse(skeleton_snapshot(frame,unavailable_reason='video_not_running')['available'])

    def test_realtime_http_uses_committed_cue_and_expires_overlay(self):
        fx=coaching.CoachingRealtimeTests();fx.setUp()
        try:
            sid,rt=fx.runtime();fx.consume(rt)
            response=fx.fx.client.get('/api/v1/sessions/'+sid)
            self.assertEqual(response.status_code,200)
            value=response.json['data'];self.assertTrue(value['skeleton']['available'])
            self.assertEqual(value['skeleton']['highlight']['cue_id'],value['coaching']['primary_cue']['cue_id'])
            self.assertEqual(value['skeleton']['frame_seq'],value['latest_frame']['frame_seq'])
            fx.fx.mono[0]+=1.01
            self.assertEqual(fx.fx.client.get('/api/v1/sessions/'+sid).json['data']['skeleton']['joints'],[])
            record=fx.fx.store.snapshot(fx.fx.user,sid,True)
            self.assertNotIn('skeleton',record)
        finally:fx.tearDown()

    def test_preview_snapshot_matches_frame_and_hides_expired_joints(self):
        fx=preview.PreviewTests();fx.setUp()
        try:
            fx.send(0);packet=fx.manager._next()
            fx.manager._publish(*packet,preview.pose(),1)
            value=fx.manager.snapshot(fx.uid,fx.pid)['result']
            self.assertTrue(value['skeleton']['available']);self.assertEqual(value['skeleton']['frame_seq'],0)
            self.assertNotIn('skeleton',fx.manager.snapshot(fx.uid,fx.pid,internal=True)['result'])
            fx.time[0]=1.01
            self.assertEqual(fx.manager.snapshot(fx.uid,fx.pid)['result']['skeleton']['joints'],[])
            with self.assertRaises(APIError):fx.manager.snapshot(s.new_id(),fx.pid)
        finally:fx.tearDown()

    def test_video_http_returns_only_latest_owned_frame_and_clears_at_end(self):
        fx=videos.VideoTests();fx.setUp();gate=threading.Event();entered=threading.Event();fx.gates.append(gate)
        class Blocking(videos.SyntheticPose):
            def detect(self,rgb,stamp):
                if stamp==200:entered.set();gate.wait(10)
                return super().detect(rgb,stamp)
        try:
            fx.manager.pose_factory=Blocking
            sid=fx.video();jid=fx.start(sid);self.assertTrue(entered.wait(10))
            response=fx.client.get('/api/v1/jobs/'+jid);self.assertEqual(response.status_code,200)
            value=response.json['data'];self.assertTrue(value['live_skeleton_available'])
            self.assertEqual(value['skeleton']['captured_ms'],value['processed_ms'])
            self.assertEqual(value['skeleton']['time_base'],'source_ms')
            self.assertFalse(value['skeleton_available'],'回看骨架仍不可用，不能混用实时状态')
            with self.assertRaises(APIError) as error:fx.manager.snapshot(s.new_id(),jid)
            self.assertEqual(error.exception.code,'NOT_FOUND')
            gate.set();done=fx.wait(jid)
            self.assertFalse(done['skeleton']['available'])
            self.assertIsNone(fx.manager.runs[jid].latest_skeleton_frame)
        finally:gate.set();fx.tearDown()


if __name__=='__main__':unittest.main()
