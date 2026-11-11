"""18-C只检查新演示来源的绑定、实际执行接续、统计隔离和失败恢复。"""
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import test_formal_records as shared
from fitness_app import db_schema as s
from fitness_app.catalogue import get_action
from fitness_app.database import StorageError
from fitness_app.paths import ROOT
from fitness_app.presentation_assets import demo_resource,MANIFEST,_read

sys.path.insert(0,str(ROOT/'scripts'))
from prepare_movement_input import cached_frames


class DemoAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.fx=shared.RecordTests();self.fx.setUp();self.client=self.fx.client;self.inputs=self.fx.app.extensions['media_inputs'];self.jobs=self.fx.app.extensions['video_jobs']

    def tearDown(self):
        self.fx.store.fault=None
        self.jobs.stop();self.fx.app.extensions['session_manager'].stop();self.fx.app.extensions['preview_manager'].stop();self.inputs.stop();self.fx.tearDown()

    def prepare(self,key=None):
        response=self.fx.post(self.client,'/api/v1/demo-inputs',{'demo_id':'barbell-row'},key=key)
        self.assertEqual(response.status_code,202,response.json);iid=response.json['data']['input_id']
        if iid in self.inputs.threads:self.inputs.threads[iid].join(10)
        data=self.inputs.snapshot(self.fx.user,iid);self.assertEqual(data['status'],'ready',data);return iid

    def config(self,iid,**changes):
        return {'action_id':'barbell_bent_over_row','capability_version':get_action('barbell_bent_over_row')['capability_version'],
            'source':'demo','mode':'free','target':None,'input_id':iid,'demo_id':'barbell-row',**changes}

    def create(self,iid):
        response=self.fx.post(self.client,'/api/v1/sessions',self.config(iid));self.assertEqual(response.status_code,201,response.json);return response.json['data']['session_id']

    def start(self,sid):
        response=self.fx.post(self.client,'/api/v1/jobs/video',{'session_id':sid,'expected_version':0});self.assertEqual(response.status_code,202,response.json)
        jid=response.json['data']['job_id'];self.jobs.runs[jid].thread.join(30);self.assertFalse(self.jobs.runs[jid].thread.is_alive());return jid,self.jobs.snapshot(self.fx.user,jid)

    def test_demo_binding_owner_idempotency_and_preprocessed_track_independence(self):
        stat=MANIFEST.stat();doc=copy.deepcopy(_read(stat.st_mtime_ns,stat.st_size));doc['demos']['barbell-row']['track']['sha256']='0'*64
        with patch('fitness_app.presentation_assets._read',return_value=doc):
            self.assertIsNone(demo_resource('barbell-row'));self.assertIsNotNone(demo_resource('barbell-row',require_track=False))
            key=s.new_id();iid=self.prepare(key);self.assertEqual(self.prepare(key),iid)
        self.assertEqual(self.inputs.snapshot(self.fx.user,iid)['encoding']['demo_id'],'barbell-row')
        wrong=self.config(iid,action_id='bodyweight_squat',capability_version=get_action('bodyweight_squat')['capability_version'])
        self.assertEqual(self.fx.post(self.client,'/api/v1/sessions',wrong).status_code,422)
        self.assertEqual(self.fx.post(self.client,'/api/v1/sessions',self.config(iid,source='video')).status_code,422)
        other=self.fx.app.test_client();self.fx.login(other,'Other_User')
        self.assertEqual(other.get('/api/v1/inputs/'+iid).status_code,404)
        self.assertEqual(self.fx.post(other,'/api/v1/sessions',self.config(iid)).status_code,422)
        path=ROOT/demo_resource('barbell-row')['video']['path']
        with path.open('rb') as stream:ordinary=self.inputs.upload(self.fx.user,self.fx.auth,stream,s.new_id())
        oid=ordinary['input_id'];self.inputs.threads[oid].join(10)
        self.assertEqual(self.fx.post(self.client,'/api/v1/sessions',self.config(oid)).status_code,422)

    def test_demo_decoder_and_cached_pose_connections_persist_actual_cycles_outside_history(self):
        poses={f['captured_ms']:f for f in cached_frames('dvids-639914-side',apply_bounds=False)};calls=[]
        class CachedPose:
            def __init__(inner,**unused):pass
            def detect(inner,rgb,stamp):
                calls.append(stamp);f=poses[stamp];count=f['framing']['person_count']
                return {'poses':[f['framing']['landmarks']] if count==1 else [[] for _ in range(count)],'model':'mediapipe-full-float16-1'}
            def close(inner):pass
        self.jobs.pose_factory=CachedPose;iid=self.prepare();sid=self.create(iid);jid,data=self.start(sid)
        self.assertEqual(data['status'],'completed',data);self.assertEqual(data['session']['source'],'demo');self.assertEqual(data['session']['total_count'],3);self.assertEqual(len(calls),855)
        self.assertFalse(self.inputs._path(iid).exists());self.assertEqual(self.fx.store.list_records(self.fx.user)['total'],0)
        own=self.client.get('/api/v1/records/'+sid+'/demo-source');self.assertEqual(own.status_code,200);self.assertIn('/static/demo/',own.json['data']['video_url'])
        other=self.fx.app.test_client();self.fx.login(other,'Other_User');self.assertEqual(other.get('/api/v1/records/'+sid+'/demo-source').status_code,404)
        normal=self.fx.running();self.fx.store.append_event(self.fx.user,normal,self.fx.event());self.assertEqual(self.fx.store.list_records(self.fx.user)['total'],1)
        self.assertEqual(self.fx.store.snapshot(self.fx.user,normal)['total_count'],1)

    def test_failed_actual_analysis_never_uses_the_three_preprocessed_events(self):
        class FailedPose:
            def __init__(inner,**unused):raise RuntimeError('D-FAULT model unavailable')
        self.jobs.pose_factory=FailedPose;iid=self.prepare();sid=self.create(iid);_,data=self.start(sid)
        self.assertEqual(data['status'],'failed');self.assertEqual(data['session']['total_count'],0);self.assertEqual(data['session']['source'],'demo')
        self.assertEqual(self.fx.store.snapshot(self.fx.user,sid,True)['events'],[]);self.assertFalse(self.inputs._path(iid).exists())

    def test_demo_save_recovery_routes_to_video_manager(self):
        iid=self.prepare();sid=self.create(iid)
        def fault(point):
            if point=='before_commit':raise StorageError('D-FAULT demo save')
        self.fx.store.fault=fault
        _,data=self.start(sid);self.fx.store.fault=None
        self.assertEqual(data['save_state'],'save_failed');called=[];original=self.jobs.recovery
        def recovery(*args,**kwargs):called.append(True);return original(*args,**kwargs)
        with patch.object(self.jobs,'recovery',side_effect=recovery):
            response=self.fx.post(self.client,'/api/v1/sessions/'+sid+'/commands',{'command':'retry_save','expected_version':data['session']['state_version']})
        self.assertEqual(response.status_code,200,response.json);self.assertEqual(called,[True]);self.assertEqual(response.json['data']['save_state'],'clean')


if __name__=='__main__':unittest.main()
