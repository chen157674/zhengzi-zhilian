"""14-F 仅已知几何→真实取景门槛／目标／保存连接，非真实视频效果。"""
import unittest
import test_formal_records as shared
from test_high_knees import settings,sequence
from fitness_app.high_knees import HighKneesCounter
from fitness_app.catalogue import get_action
from fitness_app.frame_pipeline import framing
from fitness_app import db_schema as s


class HighKneesConnections(unittest.TestCase):
    def setUp(self):
        self.fx=shared.RecordTests();self.fx.setUp();self.manager=self.fx.app.extensions['session_manager']

    def tearDown(self):
        self.manager.stop();self.fx.app.extensions['video_jobs'].stop();self.fx.app.extensions['preview_manager'].stop();self.fx.app.extensions['media_inputs'].stop();self.fx.tearDown()

    def create(self,target=None):
        config={'action_id':'high_knees','capability_version':get_action('high_knees')['capability_version'],'source':'camera','mode':'target' if target else 'free','target':target}
        response=self.fx.post(self.fx.client,'/api/v1/sessions',config);self.assertEqual(response.status_code,201,response.json)
        sid=response.json['data']['session_id'];self.manager.claim(self.fx.user,self.fx.auth,self.fx.now[0]+12*3600000,sid,s.new_id(),{'page_id':s.new_id(),'takeover':False,'expected_version':0})
        rt=self.manager.live[sid];rt.data=self.fx.store.set_state(self.fx.user,sid,'running',s.new_id());rt.adapter=HighKneesCounter(settings());rt.session_origin_mono=0
        return sid,rt

    def consume(self,rt,frames):
        for frame in frames:
            self.fx.mono[0]=(frame['captured_ms']+1)/1000
            quality=framing('high_knees',{'poses':[frame['framing']['landmarks']]});quality['ready_for_tracking']=quality['usable']
            self.assertTrue(quality['usable'],'这条已知可见腿应通过正式取景门槛')
            self.manager._consume(rt,{**frame,'framing':quality,'model_version':'D-POSE-known-geometry'})

    def test_six_total_target_completes_at_three_each_not_six_each(self):
        sid,rt=self.create({'sets':1,'value':6,'unit':'reps','rest_seconds':0});frames=[]
        for side in ('left','right')*3:frames.extend(sequence(side,len(frames)))
        self.consume(rt,frames);data=self.fx.store.snapshot(self.fx.user,sid,True)
        self.assertEqual((data['total_count'],data['left_count'],data['right_count']),(6,3,3));self.assertEqual(data['status'],'completed')
        self.assertEqual(data['config']['target']['unit'],'reps');self.assertEqual(data['groups'][0]['target_value'],6)
        self.assertEqual([e['side'] for e in data['events']],['left','right']*3);self.assertEqual(data['evaluable_count'],0)
        self.assertFalse(get_action('high_knees')['counting_available'],'D-POSE 连接不能自动开放正式能力')

    def test_missing_right_leg_still_passes_real_gate_and_preserves_left_only(self):
        sid,rt=self.create();frames=sequence();frames+=sequence(start=len(frames))
        for f in frames:
            for i in (26,28):f['framing']['landmarks'][i]['valid']=False;f['framing']['landmarks'][i]['presence']=0
        self.consume(rt,frames);data=self.fx.store.snapshot(self.fx.user,sid,True)
        self.assertEqual((data['total_count'],data['left_count'],data['right_count']),(2,2,0))


if __name__=='__main__':unittest.main()
