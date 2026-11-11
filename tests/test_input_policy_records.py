"""输入策略的新增记录与旧会话边界；隔离库，不运行真实姿态模型。"""
import json
import unittest
from sqlalchemy import update
from fitness_app import db_schema as s
from fitness_app.auth import APIError
import test_formal_records as records
import test_formal_sessions as sessions


class PolicyRecordTests(unittest.TestCase):
    def setUp(self):
        self.fx=records.RecordTests();self.fx.setUp()
    def tearDown(self):
        for name in ('video_jobs','session_manager','preview_manager','media_inputs'):
            self.fx.app.extensions[name].stop()
        self.fx.tearDown()

    def test_server_snapshot_receipt_history_and_client_override(self):
        from fitness_app.catalogue import get_action
        config={'action_id':'jumping_jack','capability_version':get_action('jumping_jack')['capability_version'],'source':'camera','mode':'free','target':None}
        key=s.new_id();created=self.fx.post(self.fx.client,'/api/v1/sessions',config,key)
        self.assertEqual(created.status_code,201,created.json);original=created.json['data'];sid=original['session_id']
        self.assertEqual(original['input_policy'],self.fx.store.processing_policy)
        self.assertEqual(len(original['input_policy']['implementation_sha256']),64)
        denied=self.fx.post(self.fx.client,'/api/v1/sessions',{**config,'input_policy':{'version':'forged'}})
        self.assertEqual(denied.status_code,400)
        self.fx.store.processing_policy={**self.fx.store.processing_policy,'version':'next-fixture-policy'}
        repeated=self.fx.post(self.fx.client,'/api/v1/sessions',config,key)
        self.assertEqual(repeated.status_code,201);self.assertEqual(repeated.json['data']['input_policy'],original['input_policy'])
        with self.assertRaises(APIError) as caught:self.fx.store.require_input_policy(original['config'])
        self.assertEqual(caught.exception.code,'STALE_INPUT_POLICY')
        with self.fx.db.transaction() as c:
            c.execute(update(s.analysis_sessions).where(s.analysis_sessions.c.id==sid).values(config_json=json.dumps(config)))
        legacy=self.fx.store.snapshot(self.fx.user,sid)
        self.assertIsNone(legacy['input_policy'],'历史记录缺策略时不能补猜成当前策略')

    def test_video_stale_policy_does_not_consume_ready_input(self):
        media=self.fx.app.extensions['media_inputs'];video=self.fx.app.extensions['video_jobs']
        from fitness_app.paths import ROOT
        from fitness_app.catalogue import get_action
        source=ROOT/'data/datasets/development-movements/jumping-jacks-commons-segment.mp4'
        with source.open('rb') as stream:
            prepared=media.upload(self.fx.user,self.fx.auth,stream,s.new_id())
        iid=prepared['input_id'];media.threads[iid].join(10)
        self.assertEqual(media.snapshot(self.fx.user,iid)['status'],'ready')
        config={'action_id':'jumping_jack','capability_version':get_action('jumping_jack')['capability_version'],'source':'video','mode':'free','target':None,'input_id':iid}
        created=self.fx.post(self.fx.client,'/api/v1/sessions',config);self.assertEqual(created.status_code,201,created.json)
        sid=created.json['data']['session_id'];self.fx.store.processing_policy={**self.fx.store.processing_policy,'version':'next-fixture-policy'}
        response=self.fx.post(self.fx.client,'/api/v1/jobs/video',{'session_id':sid,'expected_version':0})
        self.assertEqual(response.status_code,409,response.json);self.assertEqual(response.json['error']['code'],'STALE_INPUT_POLICY')
        self.assertEqual(video.runs,{})
        with self.fx.db.transaction(False) as c:
            self.assertEqual(media._row(c,self.fx.user,iid)['status'],'ready')


class PolicySessionTests(unittest.TestCase):
    def test_old_policy_can_pause_end_but_cannot_resume(self):
        fx=sessions.SessionHTTPTests();fx.setUp()
        try:
            fx.start();self.assertEqual(fx.snapshot()['input_policy']['version'],'test-override')
            fx.store.processing_policy={**fx.store.processing_policy,'version':'next-fixture-policy'}
            paused=fx.command('pause');self.assertEqual(paused.status_code,200,paused.json)
            resumed=fx.command('resume');self.assertEqual(resumed.status_code,409,resumed.json)
            self.assertEqual(resumed.json['error']['code'],'STALE_INPUT_POLICY')
            ended=fx.command('end',{'confirm':True});self.assertEqual(ended.status_code,200,ended.json)
        finally:fx.tearDown()


if __name__=='__main__':unittest.main()
