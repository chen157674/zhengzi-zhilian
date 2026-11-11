"""本人父任务及全部修订删除；真实DB/上传元数据，视觉与AI为明确替身。"""
import json,threading,time,unittest
from pathlib import Path
from unittest.mock import patch
from sqlalchemy import select,func,update
from fitness_app import db_schema as s
from fitness_app.database import StorageError
import test_video_analyses as shared

class VideoDeleteTests(unittest.TestCase):
    def setUp(self):self.f=shared.VideoAnalysesTests();self.f.setUp();self.fx=self.f.fx;self.manager=self.f.manager
    def tearDown(self):
        if hasattr(self,'provider'):self.provider.release.set()
        ai=self.fx.app.extensions['ai_jobs'];ai.stop()
        for worker in ai.workers:worker.join(5)
        self.fx.app.extensions['report_exports'].stop();self.f.tearDown()
    def completed(self):return self.f.wait(self.f.start(self.f.upload()))
    def options(self,pid):
        r=self.fx.client.get('/api/v1/video-analyses/'+pid+'/delete-options');self.assertEqual(r.status_code,200,r.json);return r.json['data']
    def remove(self,pid,options,key=None,client=None,**changes):
        c=client or self.fx.client;token=c.get('/api/v1/auth/csrf').json['data']['csrf_token']
        return c.delete('/api/v1/video-analyses/'+pid,json={'expected_version':options['expected_version'],'expected_inventory':options['inventory_version'],'confirm':True,**changes},headers={'X-CSRFToken':token,'Idempotency-Key':key or s.new_id()})
    def test_deletes_all_revisions_and_inputs_idempotently_preserving_unrelated_record(self):
        parent=self.completed();pid=parent['analysis_id'];old=parent['segments'][0];rid=self.f.revise(parent,old,'dumbbell_shoulder_press',self.f.upload());self.f.wait(rid,True)
        unrelated=self.fx.running();self.fx.finish(unrelated);options=self.options(pid);self.assertEqual(options['revision_count'],3);self.assertEqual(options['record_count'],3)
        key=s.new_id();r=self.remove(pid,options,key);self.assertEqual(r.status_code,200,r.json);self.assertTrue(r.json['data']['original_device_video_unchanged'])
        self.assertEqual(self.remove(pid,options,key).json['data'],r.json['data']);self.assertEqual(self.fx.client.get('/api/v1/video-analyses/'+pid).status_code,404)
        for sid in options['record_ids']:self.assertEqual(self.fx.client.get('/api/v1/records/'+sid).status_code,404)
        self.assertEqual(self.fx.client.get('/api/v1/records/'+unrelated).status_code,200)
        with self.fx.db.transaction(False) as c:
            self.assertEqual(c.scalar(select(func.count()).select_from(s.video_segments).where(s.video_segments.c.analysis_id==pid)),0)
            self.assertEqual(c.scalar(select(func.count()).select_from(s.media_inputs).where(s.media_inputs.c.id.in_(options['input_ids']))),0)
        mismatch=self.remove(pid,options,key,confirm=False);self.assertEqual(mismatch.status_code,422)
    def test_other_user_confirmation_and_inventory_change_do_not_delete(self):
        parent=self.completed();pid=parent['analysis_id'];options=self.options(pid)
        other=self.fx.app.test_client();self.fx.login(other,'other_user');self.assertEqual(other.get('/api/v1/video-analyses/'+pid+'/delete-options').status_code,404)
        self.assertEqual(self.remove(pid,options,client=other).status_code,404);self.assertEqual(self.remove(pid,options,confirm=False).status_code,422)
        token=self.fx.client.get('/api/v1/auth/csrf').json['data']['csrf_token']
        changed=self.fx.client.put('/api/v1/records/'+options['record_ids'][0]+'/training-note',json={'expected_version':0,'note':'new note after preview'},headers={'X-CSRFToken':token,'Idempotency-Key':s.new_id()})
        self.assertEqual(changed.status_code,200,changed.json)
        response=self.remove(pid,options);self.assertEqual(response.status_code,409,response.json);self.assertEqual(self.fx.client.get('/api/v1/video-analyses/'+pid).status_code,200)
    def test_running_revision_and_pending_storage_or_cleanup_prevent_delete(self):
        parent=self.completed();pid=parent['analysis_id'];segment=parent['segments'][0];options=self.options(pid)
        self.f.block=(threading.Event(),threading.Event());rid=self.f.revise(parent,segment,'dumbbell_shoulder_press',self.f.upload());self.assertTrue(self.f.block[0].wait(3))
        self.assertEqual(self.remove(pid,options).status_code,409);self.f.block[1].set();self.f.wait(rid,True);self.f.block=None
        with self.fx.db.transaction() as c:c.execute(update(s.video_analyses).where(s.video_analyses.c.id==pid).values(cleanup_pending=1))
        self.assertEqual(self.fx.client.get('/api/v1/video-analyses/'+pid+'/delete-options').status_code,409)
        self.manager.cleanup(self.fx.user,self.fx.auth,pid);self.options(pid)
    def test_transaction_failure_preserves_every_record_and_after_commit_receipt_recovers(self):
        parent=self.completed();pid=parent['analysis_id'];options=self.options(pid);key=s.new_id()
        with patch.object(self.fx.store,'_fault',side_effect=lambda point:(_ for _ in ()).throw(StorageError('before commit fixture')) if point=='before_commit' else None):
            self.assertEqual(self.remove(pid,options,key).status_code,503)
        self.assertEqual(self.fx.client.get('/api/v1/video-analyses/'+pid).status_code,200)
        for sid in options['record_ids']:self.assertEqual(self.fx.client.get('/api/v1/records/'+sid).status_code,200)
        with patch.object(self.fx.store,'_fault',side_effect=lambda point:(_ for _ in ()).throw(StorageError('after commit fixture')) if point=='after_commit' else None):
            self.assertEqual(self.remove(pid,options,key).status_code,503)
        receipt=self.fx.client.get('/api/v1/operations/'+key);self.assertEqual(receipt.status_code,200,receipt.json);self.assertTrue(receipt.json['data']['result']['deleted'])
        self.assertEqual(self.remove(pid,options,key).status_code,200)

    def test_late_revision_changes_inventory_even_when_parent_version_is_unchanged(self):
        parent=self.completed();pid=parent['analysis_id'];before=self.options(pid)
        rid=self.f.revise(parent,parent['segments'][0],'dumbbell_shoulder_press',self.f.upload());self.f.wait(rid,True);after=self.options(pid)
        self.assertEqual(before['expected_version'],after['expected_version']);self.assertNotEqual(before['inventory_version'],after['inventory_version'])
        self.assertEqual(self.remove(pid,before).status_code,409);self.assertEqual(self.remove(pid,after).status_code,200)

    def test_unknown_only_parent_and_hidden_unstarted_orphans_are_removable(self):
        self.f.predictor.predict=lambda *args,**kwargs:{'action_id':None,'score':.1,'model_version':'fixture-unknown'}
        parent=self.completed();pid=parent['analysis_id'];options=self.options(pid);self.assertEqual(options['record_count'],0);self.assertGreater(options['unknown_segment_count'],0);self.assertEqual(self.remove(pid,options).status_code,200)
        self.f.predictor=shared.Predictor();parent=self.completed();pid=parent['analysis_id'];sid=parent['segments'][0]['current_revision']['session_id']
        with self.fx.db.transaction() as c:
            template=dict(c.execute(select(s.analysis_sessions).where(s.analysis_sessions.c.id==sid)).mappings().one())
            for _ in range(253):
                config=json.loads(template['config_json']);config.update(video_revision_id=s.new_id(),included_in_statistics=False)
                new={**template,'id':s.new_id(),'status':'ready','started_at_ms':None,'ended_at_ms':None,'state_version':0,'config_json':json.dumps(config)}
                c.execute(s.analysis_sessions.insert().values(**new));c.execute(s.session_sets.insert().values(id=s.new_id(),session_id=new['id'],set_index=1,status='pending'))
        options=self.options(pid);self.assertEqual(options['record_count'],255);result=self.remove(pid,options);self.assertEqual(result.status_code,200,result.json);self.assertEqual(len(result.json['data']['deleted_ids']),255)

    def test_relevant_pending_write_and_finalization_prevent_delete(self):
        parent=self.completed();pid=parent['analysis_id'];sid=parent['segments'][0]['current_revision']['session_id'];writer=self.fx.writer
        writer._pending[sid]={'user_id':self.fx.user,'data':self.fx.store.snapshot(self.fx.user,sid)}
        try:self.assertEqual(self.fx.client.get('/api/v1/video-analyses/'+pid+'/delete-options').status_code,409);self.assertIn(sid,writer._pending)
        finally:writer._pending.pop(sid,None)
        self.manager.runs[pid].finalization_pending=True
        try:self.assertEqual(self.fx.client.get('/api/v1/video-analyses/'+pid+'/delete-options').status_code,409)
        finally:self.manager.runs[pid].finalization_pending=False
        self.options(pid)

    def ai(self):
        import test_ai_jobs
        self.provider=test_ai_jobs.Provider();self.fx.app.extensions['ai_provider_factory']=lambda:self.provider
        self.fx.app.extensions['ai_settings'].write(self.fx.user,self.fx.auth,{'expected_version':0,'source':'personal','personal_key':'sk-isolated-parent-delete-fixture-123456'},s.new_id())
        return self.fx.app.extensions['ai_jobs']
    def ai_wait(self,rid):
        end=time.monotonic()+5
        while time.monotonic()<end:
            row=self.fx.client.get('/api/v1/ai/requests/'+rid).json['data']
            if row['status'] in {'succeeded','failed','cancelled','uncertain'}:return row
            time.sleep(.02)
        self.fail('AI fixture did not finish')
    def test_deleting_parent_clears_ai_and_pdf_copies_but_retains_usage_and_unrelated_history(self):
        import test_report_exports
        parent=self.completed();pid=parent['analysis_id'];sid=parent['segments'][0]['current_revision']['session_id'];self.ai()
        report=self.f.post('/api/v1/ai/reports',{'kind':'review','context_kind':'record','context_id':sid,'history_scope':'none','consent':True}).json['data']['request_id'];self.assertEqual(self.ai_wait(report)['status'],'succeeded')
        service=self.fx.app.extensions['report_exports'];renderer=test_report_exports.Renderer();service.renderer=renderer
        options=service.builder.options(self.fx.user,self.fx.auth,[sid]);pdf=self.f.post('/api/v1/reports/exports',{'record_ids':[sid],'expected_versions':options['expected_versions'],'include_ai_report':True,'ai_report_ids':[report]}).json['data']['id']
        end=time.monotonic()+5
        while time.monotonic()<end:
            row=self.fx.client.get('/api/v1/reports/exports/'+pdf).json['data']
            if row['status']=='ready':break
            time.sleep(.02)
        self.assertEqual(row['status'],'ready');self.assertEqual(self.remove(pid,self.options(pid)).status_code,200)
        ai=self.fx.client.get('/api/v1/ai/reports/'+report).json['data'];self.assertIsNone(ai['result']);self.assertEqual(ai['error_code'],'AI_SOURCE_DELETED');self.assertEqual(ai['estimated_micro_cny'],140)
        self.assertEqual(self.fx.client.get('/api/v1/reports/exports/'+pdf+'/download').status_code,410)
        with self.fx.db.transaction(False) as c:
            self.assertIsNone(c.scalar(select(s.ai_requests.c.result_json).where(s.ai_requests.c.id==report)))
            self.assertIsNone(c.scalar(select(s.report_exports.c.snapshot_json).where(s.report_exports.c.id==pdf)))
            self.assertEqual(c.scalar(select(func.count()).select_from(s.stability_runs).where(s.stability_runs.c.user_id==self.fx.user)),0)

    def test_history_only_queued_report_is_cancelled_before_send_and_running_cost_is_kept(self):
        parent=self.completed();pid=parent['analysis_id'];ai=self.ai()
        with patch.object(ai,'_start_workers'):
            rid=self.f.post('/api/v1/ai/reports',{'kind':'review','context_kind':'overview','history_scope':'all','consent':True}).json['data']['request_id']
        self.assertEqual(self.remove(pid,self.options(pid)).status_code,200);row=self.ai_wait(rid);self.assertEqual(row['status'],'cancelled');self.assertEqual(row['estimated_micro_cny'],0);self.assertEqual(self.provider.calls,0)
        parent=self.completed();pid=parent['analysis_id'];sid=parent['segments'][0]['current_revision']['session_id'];self.provider.release.clear()
        rid=self.f.post('/api/v1/ai/reports',{'kind':'review','context_kind':'record','context_id':sid,'history_scope':'none','consent':True}).json['data']['request_id'];self.assertTrue(self.provider.entered.wait(3))
        self.assertEqual(self.remove(pid,self.options(pid)).status_code,200);self.provider.release.set();row=self.ai_wait(rid);self.assertEqual(row['status'],'failed');self.assertEqual(row['error_code'],'AI_SOURCE_DELETED');self.assertIsNone(row['result']);self.assertEqual(row['estimated_micro_cny'],140)

    def test_skeleton_cascade_keeps_unrelated_ai_and_original_file(self):
        import test_formal_media
        original=Path(self.fx.tmp.name)/'owned-original.mp4';content=test_formal_media.sample_mp4(frames=100);original.write_bytes(content)
        parent=self.f.wait(self.f.start(self.f.upload(original.read_bytes()),save_skeleton=True));pid=parent['analysis_id'];ids=[v['current_revision']['session_id'] for v in parent['segments']]
        self.ai();other=self.fx.running();self.fx.finish(other)
        rid=self.f.post('/api/v1/ai/reports',{'kind':'review','context_kind':'record','context_id':other,'history_scope':'none','consent':True}).json['data']['request_id'];self.assertEqual(self.ai_wait(rid)['status'],'succeeded')
        with self.fx.db.transaction(False) as c:
            self.assertGreater(c.scalar(select(func.count()).select_from(s.skeleton_tracks).where(s.skeleton_tracks.c.id.in_(ids))),0)
            self.assertGreater(c.scalar(select(func.count()).select_from(s.skeleton_chunks).where(s.skeleton_chunks.c.track_id.in_(ids))),0)
        self.assertEqual(self.remove(pid,self.options(pid)).status_code,200);self.assertEqual(original.read_bytes(),content)
        with self.fx.db.transaction(False) as c:
            self.assertEqual(c.scalar(select(func.count()).select_from(s.skeleton_tracks).where(s.skeleton_tracks.c.id.in_(ids))),0)
            self.assertEqual(c.scalar(select(func.count()).select_from(s.skeleton_chunks).where(s.skeleton_chunks.c.track_id.in_(ids))),0)
            self.assertIsNotNone(c.scalar(select(s.ai_requests.c.result_json).where(s.ai_requests.c.id==rid)))
        self.assertIsNotNone(self.fx.client.get('/api/v1/ai/reports/'+rid).json['data']['result'])

    def test_system_queued_source_delete_releases_reserved_budget_without_provider_call(self):
        parent=self.completed();pid=parent['analysis_id'];self.ai();settings=self.fx.app.extensions['ai_settings']
        admin=self.fx.app.test_client();self.fx.login(admin,'Record_Admin')
        with admin.session_transaction() as session:auth=session['_user_id']
        with self.fx.db.transaction(False) as c:uid=c.scalar(select(s.auth_sessions.c.user_id).where(s.auth_sessions.c.id==auth))
        settings.admin_write(uid,auth,{'expected_version':0,'api_key':'sk-isolated-budget-refund-fixture-123456','budget_yuan':'1.00','per_user_daily_requests':5,'enabled':True},s.new_id())
        settings.write(self.fx.user,self.fx.auth,{'expected_version':1,'source':'system'},s.new_id());ai=self.fx.app.extensions['ai_jobs']
        with patch.object(ai,'_start_workers'):rid=self.f.post('/api/v1/ai/reports',{'kind':'review','context_kind':'record','context_id':parent['segments'][0]['current_revision']['session_id'],'history_scope':'none','consent':True}).json['data']['request_id']
        with self.fx.db.transaction(False) as c:self.assertGreater(settings.policy(c)['reserved_micro_cny'],0)
        self.assertEqual(self.remove(pid,self.options(pid)).status_code,200)
        with self.fx.db.transaction(False) as c:self.assertEqual(settings.policy(c)['reserved_micro_cny'],0);self.assertEqual(settings.policy(c)['spent_micro_cny'],0)
        self.assertEqual(self.provider.calls,0);self.assertEqual(self.ai_wait(rid)['status'],'cancelled')

    def test_foreign_receipt_is_hidden_and_changed_payload_cannot_reuse_delete_key(self):
        parent=self.completed();pid=parent['analysis_id'];options=self.options(pid);key=s.new_id();self.assertEqual(self.remove(pid,options,key).status_code,200)
        other=self.fx.app.test_client();self.fx.login(other,'other_user');self.assertEqual(self.remove(pid,options,key,client=other).status_code,404)
        changed={**options,'expected_version':options['expected_version']+1};response=self.remove(pid,changed,key);self.assertEqual(response.status_code,409,response.json)

    def test_single_record_cannot_be_removed_while_its_parent_still_processes(self):
        entered=threading.Event();release=threading.Event()
        def engine(action):
            value=shared.Engine(action,self.f.control)
            if action=='conventional_deadlift':
                original=value.process
                def blocked(frame,context):entered.set();release.wait(5);return original(frame,context)
                value.process=blocked
            return value
        self.manager.engine_provider=engine;pid=self.f.start(self.f.upload())
        try:
            self.assertTrue(entered.wait(4));parent=self.manager.snapshot(self.fx.user,self.fx.auth,pid);sid=parent['segments'][0]['current_revision']['session_id'];row=self.fx.store.snapshot(self.fx.user,sid)
            response=self.f.post('/api/v1/records/delete',{'ids':[sid],'expected_versions':{sid:row['state_version']}});self.assertEqual(response.status_code,409,response.json)
            self.assertEqual(self.fx.client.get('/api/v1/records/'+sid).status_code,200)
        finally:release.set();self.f.wait(pid)

    def test_input_metadata_still_referenced_by_other_hidden_parent_record_is_kept(self):
        first=self.completed();second=self.completed();iid=first['input_id'];sid=second['segments'][0]['current_revision']['session_id']
        with self.fx.db.transaction() as c:
            template=dict(c.execute(select(s.analysis_sessions).where(s.analysis_sessions.c.id==sid)).mappings().one());config=json.loads(template['config_json'])
            config.update(input_id=iid,video_revision_id=s.new_id(),included_in_statistics=False)
            orphan={**template,'id':s.new_id(),'status':'ready','started_at_ms':None,'ended_at_ms':None,'config_json':json.dumps(config)}
            c.execute(s.analysis_sessions.insert().values(**orphan));c.execute(s.session_sets.insert().values(id=s.new_id(),session_id=orphan['id'],set_index=1,status='pending'))
        self.assertEqual(self.remove(first['analysis_id'],self.options(first['analysis_id'])).status_code,200)
        with self.fx.db.transaction(False) as c:self.assertEqual(c.scalar(select(func.count()).select_from(s.media_inputs).where(s.media_inputs.c.id==iid)),1)
        self.assertEqual(self.fx.client.get('/api/v1/video-analyses/'+second['analysis_id']).status_code,200)
        self.assertEqual(self.remove(second['analysis_id'],self.options(second['analysis_id'])).status_code,200)
        with self.fx.db.transaction(False) as c:self.assertEqual(c.scalar(select(func.count()).select_from(s.media_inputs).where(s.media_inputs.c.id==iid)),0)

if __name__=='__main__':unittest.main()
