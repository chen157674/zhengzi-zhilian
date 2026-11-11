"""真实保存周期分配、同源防重和删除来源回撤；无需模型。"""
import unittest
from sqlalchemy import select
import test_formal_records as shared
from test_training_plans import draft
from fitness_app import db_schema as s
from fitness_app.auth import APIError

class TrainingAllocationTests(unittest.TestCase):
    def setUp(self):
        self.fx=shared.RecordTests();self.fx.setUp();self.plans=self.fx.app.extensions['training_plans'];self.workouts=self.fx.app.extensions['training_workouts'];self.service=self.fx.app.extensions['training_allocations']
        value=draft(self.plans);value['sessions'][0]['items'][0]['target_reps']=1
        p=self.plans.create(self.fx.user,self.fx.auth,value,s.new_id());plan=self.plans.command(self.fx.user,self.fx.auth,p['id'],'activate',{'expected_version':0,'confirmed':True},s.new_id())
        session=plan['sessions'][0];w=self.workouts.create(self.fx.user,self.fx.auth,{'plan_session_id':session['id'],'session_expected_version':0},s.new_id());self.w=self.workouts.command(self.fx.user,self.fx.auth,w['id'],{'expected_version':0,'type':'start'},s.new_id())
        self.sid=self.fx.running();self.fx.store.append_event(self.fx.user,self.sid,self.fx.event(1),self.fx.auth);self.fx.store.append_event(self.fx.user,self.sid,self.fx.event(2),self.fx.auth);self.fx.finish(self.sid)
        self.record=self.fx.store.snapshot(self.fx.user,self.sid,True)
    def tearDown(self):self.fx.tearDown()
    def data(self,sets):return {'expected_version':self.w['state_version'],'item_id':self.w['items'][0]['item_id'],'record_id':self.sid,'record_expected_version':self.record['state_version'],'same_trainee':True,'allocations':sets}
    def test_cycle_allocation_no_duplicate_unlink_and_source_retention(self):
        payload=self.data([{'set_index':1,'cycle_ids':[1]}]);self.w=self.service.save(self.fx.user,self.fx.auth,self.w['id'],payload,s.new_id())
        self.assertEqual(self.w['completion']['done_sets'],1);self.assertEqual(self.w['source'],'record')
        with self.assertRaises(APIError):self.service.save(self.fx.user,self.fx.auth,self.w['id'],self.data([{'set_index':2,'cycle_ids':[1]}]),s.new_id())
        self.w=self.service.remove(self.fx.user,self.fx.auth,self.w['id'],self.w['record_links'][0]['id'],{'expected_version':self.w['state_version']},s.new_id())
        self.assertEqual(self.w['completion']['done_sets'],0);self.assertEqual(self.fx.store.snapshot(self.fx.user,self.sid)['session_id'],self.sid)
    def test_source_delete_invalidates_only_record_sets_and_increments_workout(self):
        self.w=self.service.save(self.fx.user,self.fx.auth,self.w['id'],self.data([{'set_index':1,'cycle_ids':[1]}]),s.new_id())
        iid=self.w['items'][0]['item_id'];self.w=self.workouts.set(self.fx.user,self.fx.auth,self.w['id'],iid,2,{'expected_version':self.w['state_version'],'actual_reps':1,'status':'done','source':'manual'},s.new_id())
        version=self.w['state_version'];self.fx.store.delete_records(self.fx.user,self.fx.auth,[self.sid],{self.sid:self.record['state_version']},s.new_id())
        changed=self.workouts.get(self.fx.user,self.fx.auth,self.w['id']);self.assertEqual(changed['state_version'],version+1)
        self.assertEqual(changed['items'][0]['sets'][0]['status'],'needs_reconciliation');self.assertEqual(changed['items'][0]['sets'][1]['source'],'manual');self.assertEqual(changed['completion']['done_sets'],1)
    def test_completed_progress_revokes_then_explicit_manual_reconciliation(self):
        self.w=self.service.save(self.fx.user,self.fx.auth,self.w['id'],self.data([{'set_index':1,'cycle_ids':[1]},{'set_index':2,'cycle_ids':[2]}]),s.new_id())
        self.w=self.workouts.command(self.fx.user,self.fx.auth,self.w['id'],{'expected_version':self.w['state_version'],'type':'complete','confirmed':True},s.new_id())
        progress=self.fx.app.extensions['training_progress'];self.assertEqual(progress.progress(self.fx.user,self.fx.auth)['completed_sessions'],1)
        self.fx.store.delete_records(self.fx.user,self.fx.auth,[self.sid],{self.sid:self.record['state_version']},s.new_id())
        self.w=self.workouts.get(self.fx.user,self.fx.auth,self.w['id']);self.assertEqual(progress.progress(self.fx.user,self.fx.auth)['completed_sessions'],0)
        self.w=self.workouts.command(self.fx.user,self.fx.auth,self.w['id'],{'expected_version':self.w['state_version'],'type':'reopen'},s.new_id())
        for index in (1,2):self.w=self.workouts.set(self.fx.user,self.fx.auth,self.w['id'],self.w['items'][0]['item_id'],index,{'expected_version':self.w['state_version'],'actual_reps':1,'status':'done','source':'manual'},s.new_id())
        self.assertEqual(self.w['completion']['done_sets'],2)
        self.w=self.workouts.command(self.fx.user,self.fx.auth,self.w['id'],{'expected_version':self.w['state_version'],'type':'complete','confirmed':True},s.new_id())
        self.assertEqual(progress.progress(self.fx.user,self.fx.auth)['completed_sessions'],1)

    def test_same_file_reanalysis_cannot_double_allocate_cycle(self):
        from sqlalchemy import update
        other=self.fx.running();self.fx.store.append_event(self.fx.user,other,self.fx.event(1),self.fx.auth);self.fx.finish(other)
        digest='a'*64
        with self.fx.db.transaction() as c:c.execute(update(s.analysis_sessions).where(s.analysis_sessions.c.id.in_([self.sid,other])).values(source='video',status='completed',source_sha256=digest,source_bytes=10,source_duration_ms=3000))
        self.record=self.fx.store.snapshot(self.fx.user,self.sid,True)
        self.w=self.service.save(self.fx.user,self.fx.auth,self.w['id'],self.data([{'set_index':1,'cycle_ids':[1]}]),s.new_id())
        payload=self.data([{'set_index':2,'cycle_ids':[1]}]);payload['record_id']=other;payload['record_expected_version']=self.fx.store.snapshot(self.fx.user,other)['state_version']
        with self.assertRaises(APIError) as caught:self.service.save(self.fx.user,self.fx.auth,self.w['id'],payload,s.new_id())
        self.assertEqual(caught.exception.code,'RECORD_ALREADY_ALLOCATED')

    def test_static_hold_interval_allocation_and_overlap_rejected(self):
        import datetime as dt
        value=draft(self.plans,'保持计划');value['sessions'][0]['items'][0].update(action_id='forearm_plank',target_reps=None,target_duration_ms=1000)
        p=self.plans.create(self.fx.user,self.fx.auth,value,s.new_id());current=self.plans.current(self.fx.user,self.fx.auth)['plan']
        self.workouts.command(self.fx.user,self.fx.auth,self.w['id'],{'expected_version':self.w['state_version'],'type':'end_early'},s.new_id())
        p=self.plans.command(self.fx.user,self.fx.auth,p['id'],'activate',{'expected_version':0,'confirmed':True,'replace_plan_id':current['id'],'replace_expected_version':current['state_version']},s.new_id())
        w=self.workouts.create(self.fx.user,self.fx.auth,{'plan_session_id':p['sessions'][0]['id'],'session_expected_version':0},s.new_id());self.w=self.workouts.command(self.fx.user,self.fx.auth,w['id'],{'expected_version':0,'type':'start'},s.new_id())
        self.sid=self.fx.running('forearm_plank');self.fx.store.append_intervals(self.fx.user,self.sid,1,[{'kind':'hold','start_ms':0,'end_ms':3000,'algorithm_version':'engineering-hold'}],self.fx.auth,3000);self.fx.finish(self.sid);self.record=self.fx.store.snapshot(self.fx.user,self.sid,True)
        self.w=self.service.save(self.fx.user,self.fx.auth,self.w['id'],self.data([{'set_index':1,'start_ms':0,'end_ms':1000}]),s.new_id());self.assertEqual(self.w['completion']['done_sets'],1)
        available=self.service.candidates(self.fx.user,self.fx.auth,self.w['id'],{'item_id':self.w['items'][0]['item_id']})['candidates'][0]
        self.assertEqual(available['hold_ranges'],[{'start_ms':1000,'end_ms':3000}]);self.assertEqual(available['available_quantity'],2000)
        with self.assertRaises(APIError):self.service.save(self.fx.user,self.fx.auth,self.w['id'],self.data([{'set_index':2,'start_ms':500,'end_ms':2000}]),s.new_id())

    def test_per_side_cycle_assignment_requires_complete_equal_sides(self):
        from sqlalchemy import update
        import json
        sid=self.fx.running('standing_dumbbell_curl')
        for seq,side in enumerate(['left','right','left'],1):
            event=self.fx.event(seq);event['side']=side;self.fx.store.append_event(self.fx.user,sid,event,self.fx.auth)
        self.fx.finish(sid);record=self.fx.store.snapshot(self.fx.user,sid,True)
        snapshot=self.w['target_snapshot'];snapshot['items'][0]['action_id']='standing_dumbbell_curl'
        with self.fx.db.transaction() as c:c.execute(update(s.training_workouts).where(s.training_workouts.c.id==self.w['id']).values(target_snapshot_json=json.dumps(snapshot)))
        self.sid=sid;self.record=record
        with self.assertRaises(APIError):self.service.save(self.fx.user,self.fx.auth,self.w['id'],self.data([{'set_index':1,'cycle_ids':[1,3]}]),s.new_id())
        self.w=self.service.save(self.fx.user,self.fx.auth,self.w['id'],self.data([{'set_index':1,'cycle_ids':[1,2]}]),s.new_id())
        self.assertEqual(self.w['items'][0]['sets'][0]['actual_reps'],1);self.assertEqual(self.w['record_links'][0]['allocated_quantity'],1)
        candidates=self.service.candidates(self.fx.user,self.fx.auth,self.w['id'],{'item_id':self.w['items'][0]['item_id']})
        remaining=next(x for x in candidates['candidates'] if x['record_id']==sid)
        self.assertEqual(remaining['available_quantity'],0);self.assertFalse(remaining['compatible'])

    def test_concurrent_same_cycle_assignments_cannot_double_count(self):
        from concurrent.futures import ThreadPoolExecutor
        payload=self.data([{'set_index':1,'cycle_ids':[1]}])
        def write(_):
            try:self.service.save(self.fx.user,self.fx.auth,self.w['id'],payload,s.new_id());return 'saved'
            except Exception:return 'rejected'
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(write,range(2)))
        self.assertEqual(results.count('saved'),1)
        current=self.workouts.get(self.fx.user,self.fx.auth,self.w['id']);self.assertEqual(len(current['record_links']),1);self.assertEqual(current['completion']['done_sets'],1)

    def test_reference_link_never_increments_completion(self):
        payload=self.data([]);payload['reference_only']=True
        self.w=self.service.save(self.fx.user,self.fx.auth,self.w['id'],payload,s.new_id())
        self.assertEqual(self.w['completion']['done_sets'],0);self.assertEqual(self.w['record_links'][0]['unit'],'reference');self.assertEqual(self.w['record_links'][0]['allocated_quantity'],0)

    def test_candidates_exclude_consumed_cycles_and_respect_search_date_and_paging(self):
        self.w=self.service.save(self.fx.user,self.fx.auth,self.w['id'],self.data([{'set_index':1,'cycle_ids':[1]}]),s.new_id())
        query={'item_id':self.w['items'][0]['item_id']}
        candidate=next(x for x in self.service.candidates(self.fx.user,self.fx.auth,self.w['id'],query)['candidates'] if x['record_id']==self.sid)
        self.assertEqual(candidate['cycle_ids'],[2]);self.assertEqual(candidate['available_quantity'],1)
        self.assertEqual(candidate['cycles'][0]['side'],'none')
        self.assertEqual(self.service.candidates(self.fx.user,self.fx.auth,self.w['id'],{**query,'query':'不存在的动作'})['candidates'],[])
        with self.assertRaises(APIError):self.service.candidates(self.fx.user,self.fx.auth,self.w['id'],{**query,'date_from':'2020-01-01','date_to':'2026-01-01'})
        for _ in range(20):
            sid=self.fx.running();self.fx.finish(sid)
        first=self.service.candidates(self.fx.user,self.fx.auth,self.w['id'],query)
        second=self.service.candidates(self.fx.user,self.fx.auth,self.w['id'],{**query,'page':2})
        self.assertTrue(first['has_more']);self.assertEqual(first['page'],1);self.assertEqual(len(first['candidates']),20)
        self.assertFalse(second['has_more']);self.assertEqual(second['page'],2);self.assertEqual(len(second['candidates']),1)
        self.assertFalse({x['record_id'] for x in first['candidates']}&{x['record_id'] for x in second['candidates']})

    def test_late_entry_requires_explicit_plan_date_and_confirmation(self):
        from sqlalchemy import update
        with self.fx.db.transaction() as c:c.execute(update(s.analysis_sessions).where(s.analysis_sessions.c.id==self.sid).values(started_at_ms=self.fx.now[0]-86400000))
        self.record=self.fx.store.snapshot(self.fx.user,self.sid,True)
        payload=self.data([{'set_index':1,'cycle_ids':[1]}])
        with self.assertRaises(APIError) as caught:self.service.save(self.fx.user,self.fx.auth,self.w['id'],payload,s.new_id())
        self.assertEqual(caught.exception.code,'RECORD_DATE_MISMATCH')
        payload.update(performed_date=self.w['target_snapshot']['date'],late_entry_confirmed=True)
        self.w=self.service.save(self.fx.user,self.fx.auth,self.w['id'],payload,s.new_id())
        self.assertEqual(self.w['completion']['done_sets'],1)

    def test_unknown_variant_is_not_advertised_as_compatible(self):
        import json
        from sqlalchemy import update
        target=self.w['target_snapshot'];target['items'][0]['variant']='特定动作变式'
        with self.fx.db.transaction() as c:c.execute(update(s.training_workouts).where(s.training_workouts.c.id==self.w['id']).values(target_snapshot_json=json.dumps(target)))
        data=self.service.candidates(self.fx.user,self.fx.auth,self.w['id'],{'item_id':self.w['items'][0]['item_id']})
        value=next(x for x in data['candidates'] if x['record_id']==self.sid)
        self.assertFalse(value['compatible']);self.assertIn('动作变式无法确认或不一致',value['reasons'])

    def test_candidates_only_real_owned_quantities(self):
        result=self.service.candidates(self.fx.user,self.fx.auth,self.w['id'],{'item_id':self.w['items'][0]['item_id']})
        selected=next(x for x in result['candidates'] if x['record_id']==self.sid)
        self.assertEqual(selected['cycle_ids'],[1,2]);self.assertEqual(selected['available_quantity'],2)
        bad=self.data([{'set_index':1,'cycle_ids':[999]}])
        with self.assertRaises(APIError):self.service.save(self.fx.user,self.fx.auth,self.w['id'],bad,s.new_id())
        with self.fx.db.transaction(False) as c:self.assertEqual(len(c.execute(select(s.training_record_allocations)).all()),0)

if __name__=='__main__':unittest.main()
