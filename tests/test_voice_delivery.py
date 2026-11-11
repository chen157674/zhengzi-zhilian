import copy
import unittest
from unittest.mock import patch

import test_formal_records as shared
from test_action_counting import geometry
from fitness_app.frame_pipeline import framing
from fitness_app import db_schema as s


class VoiceDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.fx=shared.RecordTests();self.fx.setUp();self.manager=self.fx.app.extensions['session_manager'];self.sid=self.fx.running()
        self.rt=self.manager._runtime(self.fx.user,self.sid,self.fx.auth,self.fx.now[0]+3600000)

    def tearDown(self):
        self.fx.app.extensions['video_jobs'].stop();self.manager.stop();self.fx.app.extensions['preview_manager'].stop();self.fx.app.extensions['media_inputs'].stop();self.fx.tearDown()

    def test_candidate_metadata_never_publishes_quality_feedback(self):
        event={**self.fx.event(),'assessments':[{'code':'squat.range','status':'issue','start_ms':0,'end_ms':1000}]}
        self.manager._publish_feedback(self.rt,event=event);self.assertEqual(self.rt.feedback,[])
        self.assertEqual(self.fx.client.get('/api/v1/voice?action_id=bodyweight_squat').json['data']['available_count'],0)

    def test_test_only_verified_metadata_retains_text_age_but_pause_clears_delivery(self):
        # 仅修改隔离运行对象以验消息结构；不修改正式能力文件或数据库里的判项状态。
        self.rt.data=copy.deepcopy(self.rt.data);self.rt.data['supported_items']=[{'code':'squat.range','status':'verified'}]
        event={**self.fx.event(),'assessments':[{'code':'squat.range','status':'issue','start_ms':0,'end_ms':1000}]}
        self.manager._publish_feedback(self.rt,event=event);self.assertEqual(self.rt.feedback[0]['sequence'],1)
        self.fx.mono[0]=1.2;result=self.manager.decorate_snapshot(self.fx.user,self.rt.data)
        self.assertEqual(result['feedback_age_ms'],1200);self.assertEqual(len(result['feedback']),1,'文字可保留；客户端语音按消息年龄拒绝迟到播放')
        self.assertEqual(result['feedback_epoch'],result['control'].get('stream_epoch'))
        self.manager._set(self.rt,'paused');self.assertEqual(self.rt.feedback,[])

    def test_failed_save_never_reaches_feedback_publication(self):
        class Adapter:
            def process(inner,frame,context):return {'events':[{'side':'none','start_ms':0,'end_ms':100,'algorithm_version':'D-EVENT-voice','feature_schema_version':'D-EVENT','assessments':[]}]}
            def reset(inner,reason):pass
        self.rt.adapter=Adapter();frame=geometry('bodyweight_squat',170,seq=1);frame['captured_ms']=200
        frame['framing']=framing('bodyweight_squat',{'poses':[frame['framing']['landmarks']]});frame['framing']['ready_for_tracking']=True
        self.fx.mono[0]=.2
        with patch.object(self.fx.writer,'submit',return_value={**self.rt.data,'save_state':'save_failed'}),patch.object(self.manager,'_publish_feedback') as publish:
            self.manager._consume(self.rt,frame);publish.assert_not_called()
        self.assertTrue(self.rt.frozen);self.assertEqual(self.rt.feedback,[])


if __name__=='__main__':unittest.main()
