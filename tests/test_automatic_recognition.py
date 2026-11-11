"""自动优先观察入口与稳定确认，不把受控预测当真实准确率。"""
import io
import json
import hashlib
import unittest
from unittest.mock import patch,Mock
import numpy as np
from fitness_app.automatic_recognition import ActionRecognizer,window_features,decision,CONFIG,prepare_frame
from fitness_app import db_schema as s
from fitness_app.catalogue import get_action
from test_action_counting import geometry
from test_plank_hold import geometry as static_plank
import test_formal_records as records
import test_formal_preview as preview


class Predicted:
    classes_=np.array(['bodyweight_squat','conventional_deadlift','standard_push_up','standing_dumbbell_curl'])
    def __init__(self):self.scores=[.02,.94,.02,.02]
    def predict_proba(self,x):return np.array([self.scores for _ in x])


def moving(i):
    frame=geometry('conventional_deadlift',80+(i%20)*4,seq=i)
    frame['captured_ms']=i*100
    return frame


class RecognitionTests(unittest.TestCase):
    def test_failed_development_gate_keeps_auto_unavailable_and_manual_fallback(self):
        model=Mock();model.is_file.return_value=True;model.read_bytes.return_value=b'fixture'
        manifest=Mock();manifest.is_file.return_value=True
        manifest.read_text.return_value=json.dumps({'config':CONFIG,'sha256':hashlib.sha256(b'fixture').hexdigest(),'development_gate_passed':False})
        with patch('fitness_app.automatic_recognition.MODEL',model),patch('fitness_app.automatic_recognition.MANIFEST',manifest),patch('joblib.load') as load:
            recognizer=ActionRecognizer();value=recognizer.process(moving(0))
            load.assert_not_called();self.assertFalse(value['available']);self.assertIsNone(value['action_id'])
            self.assertEqual(value['reason'],'model_validation_pending');self.assertTrue(value['manual_fallback_available'])

    def test_static_missing_and_temporal_gap_reject_without_fabricated_pose(self):
        frames=[moving(i) for i in range(21)]
        features,reason=window_features(frames)
        self.assertIsNone(reason);self.assertEqual(len(features),48)
        static=[{**moving(0),'frame_seq':i,'captured_ms':i*100} for i in range(21)]
        self.assertEqual(window_features(static)[1],'stationary')
        self.assertEqual(window_features([f for f in frames if f['captured_ms']<500 or f['captured_ms']>1200])[1],'pose_gap')
        for f in frames:f['framing']['person_count']=2
        self.assertEqual(window_features(frames)[1],'insufficient_pose')

    def test_partial_leg_occlusion_keeps_classification_features_without_fabricating_joints(self):
        frames=[{**geometry('standing_dumbbell_curl',80+i*4,seq=i),'captured_ms':i*100} for i in range(21)]
        for frame in frames:
            for i in (25,26,27,28):frame['framing']['landmarks'][i]={'valid':False}
        feature,reason=window_features(frames)
        # 该几何上肢与躯干有变化；不可见膝/踝特征须保留缺失，而非补点。
        self.assertIsNone(reason);self.assertEqual(len(feature),48)
        self.assertTrue(np.isnan(feature[5:15]).all())
        self.assertEqual(feature[-8:].tolist(),[1,0,0,1,1,1,0,1])
        prepared=window_features([prepare_frame(f) for f in frames])[0]
        np.testing.assert_allclose(feature,prepared,equal_nan=True)
        self.assertEqual(frames[0]['framing']['landmarks'][25],{'valid':False})

    def test_right_only_motion_is_not_lost_when_left_side_is_equally_visible(self):
        frames=[{**geometry('standing_dumbbell_curl',170,right=60+i*5,seq=i),'captured_ms':i*100} for i in range(21)]
        feature,reason=window_features(frames)
        self.assertIsNone(reason)
        self.assertAlmostEqual(feature[0],110.)
        self.assertGreater(feature[1],20.)

    def test_static_support_pose_can_enter_classifier_without_counting_a_rep(self):
        frames=[static_plank(i) for i in range(21)]
        feature,reason=window_features(frames)
        self.assertIsNone(reason);self.assertEqual(len(feature),48)
        self.assertTrue(all('events' not in f for f in frames))
        self.assertEqual(window_features([{**moving(0),'captured_ms':i*100} for i in range(21)])[1],'stationary')

    def test_expanded_sources_are_deduplicated_and_holdout_groups_do_not_overlap(self):
        from fitness_app.recognition_inputs import extra_inputs,extra_frames
        sources=extra_inputs()
        self.assertEqual(len(sources),7);self.assertEqual(len({x['source_sha256'] for x in sources}),7)
        self.assertFalse({x['group'] for x in sources if x['partition']=='train'} & {x['group'] for x in sources if x['partition']=='validation'})
        plank=next(x for x in sources if x['action']=='forearm_plank')
        frames=extra_frames(plank)
        first=frames[0]['captured_ms']
        self.assertIsNotNone(window_features([f for f in frames if first<=f['captured_ms']<=first+2000])[0])

    def test_confirmation_switch_and_target_loss_are_explicit(self):
        recognizer=ActionRecognizer();recognizer.model=Predicted();recognizer.reason=None;recognizer.version='D-REC'
        for i in range(29):result=recognizer.process(moving(i))
        self.assertEqual(result['state'],'recognized');self.assertEqual(result['action_id'],'conventional_deadlift')
        recognizer.model.scores=[.94,.02,.02,.02]
        for i in range(29,33):result=recognizer.process(moving(i))
        self.assertIsNone(result['action_id']);self.assertEqual(result['state'],'confirming')
        for i in range(33,42):result=recognizer.process(moving(i))
        self.assertEqual(result['action_id'],'bodyweight_squat')
        frame=moving(42);frame['framing']['person_count']=0
        self.assertIsNone(recognizer.process(frame)['action_id']);self.assertEqual(len(recognizer.rows),0)
        recognizer.model.scores=[.4,.3,.2,.1]
        self.assertIsNone(decision(recognizer.model,np.zeros(35))['candidate_action_id'])

    def test_http_defaults_auto_manual_fallback_and_no_preview_records(self):
        fx=records.RecordTests();fx.setUp();manager=fx.app.extensions['preview_manager']
        try:
            with patch.object(manager,'start'):
                response=fx.post(fx.client,'/api/v1/previews',{'page_id':s.new_id()})
                self.assertEqual(response.status_code,201,response.json);data=response.json['data']
                self.assertEqual(data['recognition_mode'],'auto');pid=data['preview_id']
                metadata={'stream_epoch':data['stream_epoch'],'frame_seq':0,'captured_ms':0,'width':32,'height':24}
                manager.submit(fx.user,pid,data['preview_token'],metadata,preview.jpeg())
                packet=manager._next();manager._publish(*packet,preview.pose(),1)
                value=fx.client.get('/api/v1/previews/'+pid).json['data']
                self.assertIsNone(value['action_id']);self.assertEqual(value['result']['recognition']['mode'],'auto')
                self.assertTrue(value['result']['recognition']['manual_fallback_available'])
                fx.mono[0]+=1.1
                value=fx.client.get('/api/v1/previews/'+pid).json['data']['result']
                self.assertEqual(value['recognition']['reason'],'stale_frame');self.assertEqual(value['skeleton']['joints'],[])
                self.assertEqual(fx.client.get('/api/v1/records').json['data']['total'],0)
                manager.release(fx.user,pid,{'preview_token':data['preview_token'],'stream_epoch':data['stream_epoch']},s.new_id())
                self.assertIsNone(manager.history[pid]['recognizer'])
                manual=fx.post(fx.client,'/api/v1/previews',{'recognition_mode':'manual','page_id':s.new_id(),
                    'action_id':'bodyweight_squat','capability_version':get_action('bodyweight_squat')['capability_version']})
                self.assertEqual(manual.status_code,201);self.assertEqual(manual.json['data']['recognition_mode'],'manual')
                mixed=fx.post(fx.client,'/api/v1/previews',{'recognition_mode':'auto','page_id':s.new_id(),'action_id':'bodyweight_squat'})
                self.assertEqual(mixed.status_code,422)
        finally:
            for key in ('video_jobs','session_manager','preview_manager','media_inputs'):fx.app.extensions[key].stop()
            fx.tearDown()


if __name__=='__main__':unittest.main()
