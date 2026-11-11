"""完整1694演示入口→真实身体/手部输入→新增指导保存，不注入模型替身。"""
import json,os,time,unittest
from unittest.mock import patch
from fitness_app.paths import ROOT
from fitness_app.posture_rules import policy_version
import test_formal_records as fixtures

class MovementVideoTests(unittest.TestCase):
    def setUp(self):
        self.env=patch.dict(os.environ,{'FITNESS_MOVEMENT_GUIDANCE':'1','FITNESS_BODY_LEAN':'1','FITNESS_PRETRAINED_CORRECTION':'0'});self.env.start();policy_version.cache_clear()
        self.fx=fixtures.RecordTests();self.fx.setUp()
    def tearDown(self):
        try:
            for callback in self.fx.app.extensions.get('stop_hooks',[]):callback()
            self.fx.tearDown()
        finally:self.env.stop();policy_version.cache_clear()
    def test_real_demo_guidance_items_and_saved_return_observations(self):
        from fitness_app.catalogue import get_action
        from fitness_app.presentation_assets import demo_resource
        from fitness_app.research_extract import atomic_json
        from fitness_app.rtmpose_input import cpu_thread_count
        began=time.perf_counter()
        fx=self.fx;inputs=fx.app.extensions['media_inputs'];jobs=fx.app.extensions['video_jobs']
        response=fx.post(fx.client,'/api/v1/demo-inputs',{'demo_id':'user-1694'});self.assertEqual(response.status_code,202,response.json)
        iid=response.json['data']['input_id'];inputs.threads[iid].join(15)
        self.assertEqual(inputs.snapshot(fx.user,iid)['status'],'ready')
        action=get_action('machine_chest_press')
        response=fx.post(fx.client,'/api/v1/sessions',{'input_id':iid,'action_id':action['action_id'],'capability_version':action['capability_version'],
            'source':'demo','mode':'free','target':None,'demo_id':'user-1694','coaching_view':'auto'})
        self.assertEqual(response.status_code,201,response.json);sid=response.json['data']['session_id']
        response=fx.post(fx.client,'/api/v1/jobs/video',{'session_id':sid,'expected_version':0});self.assertEqual(response.status_code,202,response.json)
        job=response.json['data']['job_id'];run=jobs.runs[job];deadline=time.monotonic()+360
        while run.thread.is_alive() and time.monotonic()<deadline:run.thread.join(1)
        self.assertFalse(run.thread.is_alive());result=jobs.snapshot(fx.user,job)
        self.assertEqual(result['status'],'completed',result.get('error_code'))
        record=fx.store.snapshot(fx.user,sid,True);findings=record['posture_observations']
        for suffix in ('phase_lag','fast_return','wrist_bend'):
            self.assertTrue(any(i['code'].endswith(suffix) for i in findings),suffix)
        returns=[i for i in findings if i['code'].endswith('fast_return')]
        self.assertTrue(returns)
        for item in returns:
            if item['status'] in ('issue','not_observed'):
                evidence=item['evidence']
                self.assertGreaterEqual(evidence['return_observed_samples'],3)
                self.assertTrue(.8<=evidence['return_excursion_degrees']/evidence['effort_excursion_degrees']<=1.25)
            else:
                self.assertIn(item['reason'],{'awaiting_complete_cycle','incomplete_return_evidence',
                    'return_range_not_comparable','return_samples_insufficient','unsupported_view'})
        self.assertTrue(any(i['code'].endswith('pretrained_upper_body_balance') and i['status']=='issue' for i in findings))
        wrist_issues=[i for i in findings if i['code'].endswith('wrist_bend') and i['status']=='issue']
        self.assertTrue(wrist_issues,'真实原片必须有已保存折腕候选，不能只凭补检数量通过')
        self.assertTrue(all(i['evidence'].get('contact_with_handle_verified') is False for i in wrist_issues))
        self.assertFalse(inputs._path(iid).exists());self.assertEqual(fx.store.list_records(fx.user)['total'],0)
        report={'scope':'real_local_source_video_and_models_not_specific_error_truth','status':result['status'],'source':'user-1694',
            'source_sha256':demo_resource('user-1694')['video']['sha256'],'policy_version':record['posture_policy_version'],
            'saved_guidance':[i for i in findings if i['code'].endswith(('fast_return','phase_lag','wrist_bend'))],
            'source_copy_cleaned':True,'excluded_from_personal_statistics':True,'paid_api_calls':0,
            'elapsed_seconds':time.perf_counter()-began,'pose_cpu_threads':cpu_thread_count()}
        atomic_json(ROOT/'data/verification/round3/movement-guidance-upload-review.json',report)

if __name__=='__main__':unittest.main()