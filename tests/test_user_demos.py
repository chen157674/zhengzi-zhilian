"""用户演示的素材身份、白名单、播放接口和实际分析；不将示范当作正确姿态真值。"""
import copy
import hashlib
import json
import unittest
from unittest.mock import patch

import av
from fitness_app import db_schema as s
from fitness_app.catalogue import get_action
from fitness_app.paths import ROOT
from fitness_app.presentation_assets import MANIFEST,_read,demo_resource,demo_catalogue,home_demo_catalogue,demo_display_track
from fitness_app.research_extract import atomic_json
import test_formal_records as shared


class UserDemoAssetsTests(unittest.TestCase):
    def test_default_and_registered_video_formats_match(self):
        default=demo_resource()
        self.assertEqual(default['id'],'user-1694')
        self.assertEqual(default['action_id'],'machine_chest_press')
        self.assertEqual([d['id'] for d in demo_catalogue()],['user-1575','user-1694','user-1688','user-1689'])
        self.assertEqual([d['id'] for d in home_demo_catalogue()],['user-1694','user-1689'])
        for item in demo_catalogue():
            with self.subTest(id=item['id']),av.open(str(ROOT/item['video']['path'])) as container:
                self.assertEqual(container.streams.video[0].codec_context.name,'h264')
                self.assertEqual(len(container.streams.audio),0)
                frame=next(container.decode(video=0))
                self.assertEqual(frame.rotation,0)
                self.assertTrue(all(c.bits==8 for c in frame.format.components))
        track=json.loads((ROOT/default['track']['path']).read_text(encoding='utf-8'))
        self.assertEqual(track['source_sha256'],default['video']['sha256'])
        self.assertEqual((track['width'],track['height']),(default['width'],default['height']))
        self.assertTrue(track['visible_frames']>0)
        self.assertEqual(track['events'],[])
        self.assertEqual(track['mode'],'preprocessed_user_source_not_live')
        self.assertEqual(track['feedback_schema_version'],1)
        self.assertTrue(all(f['feedback']['state'] in {'clear','issue','observing','unassessable'} for f in track['frames']))
        states={f['feedback']['state'] for f in track['frames']}
        self.assertTrue(states.issubset({'clear','issue','observing','unassessable'}))

    def test_landscape_pair_preserves_source_and_annotation_meanings(self):
        for number,quality,label in [('1688','needs_adjustment','错误演示'),('1689','correct_reference','正确演示')]:
            item=demo_resource('user-'+number)
            self.assertEqual(item['action_id'],'lat_pulldown')
            self.assertEqual(item['display_title'],'高位下拉（'+label+'）')
            self.assertEqual((item['width'],item['height']),(1280,720))
            self.assertEqual(item['conversion']['foreground_crop_pixels'],[0,480,1080,1440])
            self.assertFalse(item['conversion']['duration_trimmed'])
            self.assertEqual(item['user_reported_quality'],quality)
            self.assertFalse(item['independent_error_truth'])
            track=json.loads((ROOT/item['track']['path']).read_text(encoding='utf-8'))
            self.assertEqual(track['classification_source'],'user_confirmed_action')
            self.assertEqual(track['source_sha256'],item['video']['sha256'])
            self.assertEqual(track['visible_frames'],len(track['frames']))
            if number=='1688':
                self.assertEqual(track['user_reported_issues'],['身体侧倾'])
                self.assertTrue(all(f['feedback'].get('clear_label')=='你标记的问题尚未检出' for f in track['frames'] if f['feedback']['state']=='clear'))

    def test_confirmed_pulldown_range_binds_exact_demo_source_without_changing_detection(self):
        from fitness_app.presentation_assets import confirmed_demo_findings
        item=demo_resource('user-1688')
        record={'source':'demo','action_id':item['action_id'],'source_sha256':item['video']['sha256'],'config':{'demo_id':item['id']}}
        findings=confirmed_demo_findings(record)
        self.assertEqual(len(findings),1);self.assertEqual(findings[0]['start_ms'],2000)
        self.assertEqual(findings[0]['end_ms'],item['duration_ms']);self.assertFalse(findings[0]['model_detected'])
        self.assertEqual(confirmed_demo_findings({**record,'source_sha256':'0'*64}),[])
        self.assertEqual(confirmed_demo_findings({**record,'source':'video'}),[])
        self.assertEqual(confirmed_demo_findings({**record,'config':{'demo_id':'user-1689'}}),[])

    def test_new_tracks_keep_real_results_and_user_labels_separate(self):
        for number in ('1692','1693','1694'):
            item=demo_resource('user-'+number)
            track=json.loads((ROOT/item['track']['path']).read_text(encoding='utf-8'))
            self.assertEqual(track['source_sha256'],item['video']['sha256'])
            self.assertEqual(track['classification_source'],'formal_offline_scan_and_segments')
            if (track.get('body_lean_only') or track.get('movement_guidance')) and number in ('1693','1694'):
                issues=[row for row in track['frames'] if row['feedback']['state']=='issue']
                self.assertTrue(issues,'已知侧倾素材必须有来自真实规则的可见反馈')
                self.assertTrue(any(row['feedback'].get('highlight') for row in issues))
            elif not track.get('body_lean_only') and not track.get('movement_guidance'):
                self.assertFalse(any(row['feedback']['state']=='issue' for row in track['frames']))
            if number in ('1693','1694'):
                self.assertEqual(item['user_reported_issues'],['身体侧倾','握持不当'])
                self.assertTrue(all(row['feedback'].get('clear_label')=='你标记的问题尚未检出' for row in track['frames'] if row['feedback']['state']=='clear'))

    def test_persistent_annotation_preserves_original_feedback_and_coordinates(self):
        item=demo_resource('user-1694');path=ROOT/item['track']['path'];original=path.read_bytes()
        raw=json.loads(original);display=demo_display_track(item)
        self.assertEqual(hashlib.sha256(original).hexdigest(),item['track']['sha256'])
        self.assertTrue(display['model_results_unchanged'])
        self.assertEqual(len(display['frames']),len(raw['frames']))
        for before,after in zip(raw['frames'],display['frames']):
            self.assertEqual(after['model_feedback'],before['feedback'])
            self.assertEqual(after['p'],before['p'])
            self.assertEqual(after['cue'],before['cue'])
            feedback=after['feedback']
            if before['t']<item['display_annotation']['start_ms']:
                self.assertIn(feedback['state'],('observing','unassessable'))
                self.assertEqual(feedback['title'],'正在分析')
                self.assertFalse(feedback['checks'])
                self.assertEqual(feedback['instruction'],'')
            elif feedback['state']=='issue':
                self.assertEqual(feedback['title'],'身体侧倾、握持不当')
            self.assertEqual(feedback['display_basis'],'user_confirmed_demo_annotation')
            self.assertIsNone(feedback['overall_correctness'])
            if feedback['highlight']:
                self.assertEqual(after['people'],1)
                self.assertTrue(all(after['p'][i] is not None for i in feedback['highlight']['joint_indices']))
        self.assertEqual(path.read_bytes(),original)
        self.assertEqual(display['feedback_state_counts'],raw['feedback_state_counts'])
        self.assertTrue(any(f['feedback']['state']=='issue' for f in display['frames']))
        self.assertIsNone(demo_display_track(demo_resource('user-1575')))

    def test_annotation_does_not_invent_missing_or_ambiguous_points(self):
        raw={'frames':[{'t':3000,'feedback':{'state':'unassessable'},'p':[None]*33,'people':1},
                       {'t':3100,'feedback':{'state':'unassessable'},'p':[[.5,.5]]*33,'people':2}]}
        item=demo_resource('user-1694')
        with patch('pathlib.Path.read_text',return_value=json.dumps(raw)):
            display=demo_display_track(item)
        for frame in display['frames']:
            self.assertEqual(frame['feedback']['state'],'unassessable')
            self.assertIsNone(frame['feedback']['highlight'])

    def test_annotation_requires_continuous_visibility_and_resets_after_a_gap(self):
        item=demo_resource('user-1694')
        raw={'frames':[{'t':t,'feedback':{'state':'observing'},'p':[[.5,.5]]*33,'people':people}
            for t,people in [(t,1) for t in range(1200,3001,100)]+[(3100,2)]+[(t,1) for t in range(3200,4101,100)]]}
        with patch('pathlib.Path.read_text',return_value=json.dumps(raw)):
            display=demo_display_track(item)
        states={row['t']:row['feedback']['state'] for row in display['frames']}
        self.assertEqual(states[1900],'observing');self.assertEqual(states[2000],'issue')
        self.assertEqual(states[3100],'unassessable');self.assertEqual(states[3900],'observing')
        self.assertEqual(states[4000],'issue')
        bad=copy.deepcopy(item);bad['display_annotation']['start_ms']=-1
        with self.assertRaises(ValueError):demo_display_track(bad)

    def test_chest_press_trim_matches_its_video_and_track(self):
        item=demo_resource('user-1694')
        self.assertEqual(item['conversion']['trim_start_ms'],5500)
        self.assertTrue(item['conversion']['duration_trimmed'])
        self.assertEqual(item['duration_ms'],21067)
        raw=json.loads((ROOT/item['track']['path']).read_text(encoding='utf-8'))
        self.assertEqual(raw['timeline_edit']['trim_start_ms'],5500)
        self.assertTrue(raw['timeline_edit']['model_feedback_reused_from_original_timeline'])
        self.assertLess(raw['frames'][0]['t'],100)
        self.assertLess(raw['frames'][-1]['t'],item['duration_ms'])
        self.assertEqual(len(raw['frames']),item['frames'])
        self.assertEqual(raw['source_sha256'],item['video']['sha256'])
        self.assertEqual(sum(raw['posture_status_counts'].values()),sum(len(f['feedback']['checks']) for f in raw['frames']))
        def evidence_times(value):
            if isinstance(value,list):
                for v in value:yield from evidence_times(v)
            elif isinstance(value,dict):
                for k,v in value.items():
                    if k in ('start_ms','end_ms','evidence_start_ms','evidence_end_ms') and type(v) in (int,float):yield v
                    else:yield from evidence_times(v)
        self.assertTrue(all(-5500<=t<item['duration_ms'] for f in raw['frames'] for t in evidence_times(f['feedback'])))

    def test_display_legs_require_observed_hip_knee_and_ankle(self):
        for number in ('1688','1689'):
            item=demo_resource('user-'+number)
            raw=json.loads((ROOT/item['track']['path']).read_text(encoding='utf-8'))
            refinement=raw['skeleton_refinement'];self.assertTrue(refinement['model_feedback_unchanged'])
            for row in raw['frames']:
                self.assertEqual(row['p'][:25],row['model_p'][:25])
                self.assertEqual(row['p'][29:],[None]*4)
                for hip,knee,ankle,ids in [(23,25,27,(0,2,4)),(24,26,28,(1,3,5))]:
                    self.assertEqual(row['p'][knee] is None,row['p'][ankle] is None)
                    if row['p'][knee] is None:continue
                    self.assertIsNotNone(row['p'][hip])
                    observed=row['leg_observation']
                    self.assertTrue(all(observed[i]['coordinate_valid'] and observed[i]['score']>=.5 for i in ids))
                    for target,source in [(knee,ids[1]),(ankle,ids[2])]:
                        self.assertEqual(row['p'][target],[round(observed[source][c],6) for c in ('x','y')])

    def test_annotation_changes_cache_identity(self):
        item=demo_resource('user-1694')
        stat=MANIFEST.stat();doc=copy.deepcopy(_read(stat.st_mtime_ns,stat.st_size))
        doc['demos']['user-1694']['display_annotation']['instruction']='保持躯干稳定。'
        with patch('fitness_app.presentation_assets._read',return_value=doc):
            changed=demo_resource('user-1694')
        self.assertNotEqual(item['track_url'],changed['track_url'])
        self.assertNotEqual(item['track_etag'],changed['track_etag'])
        self.assertEqual(item['video_url'],changed['video_url'])

    def test_external_path_and_changed_track_are_not_served(self):
        stat=MANIFEST.stat();doc=_read(stat.st_mtime_ns,stat.st_size)
        for change in ('path','hash'):
            bad=copy.deepcopy(doc)
            if change=='path':bad['demos']['user-1575']['video']['path']=r'E:\08_Downloads\LocalSend\IMG_1575.MOV'
            else:bad['demos']['user-1575']['track']['sha256']='0'*64
            with self.subTest(change=change),patch('fitness_app.presentation_assets._read',return_value=bad):
                self.assertIsNone(demo_resource('user-1575'))
        for key in ('../user-1575','E:\\08_Downloads','unregistered',[]):self.assertIsNone(demo_resource(key))


class UserDemoHTTPTests(unittest.TestCase):
    def test_confirmed_demo_range_is_in_record_and_ai_evidence_with_source_identity(self):
        from fitness_app.ai_context import AIContextBuilder
        item=demo_resource('user-1688');inputs=self.fx.app.extensions['media_inputs']
        response=self.fx.post(self.fx.client,'/api/v1/demo-inputs',{'demo_id':item['id']})
        self.assertEqual(response.status_code,202,response.json);iid=response.json['data']['input_id']
        inputs.threads[iid].join(15)
        response=self.fx.post(self.fx.client,'/api/v1/sessions',{'input_id':iid,'demo_id':item['id'],
            'action_id':'lat_pulldown','capability_version':get_action('lat_pulldown')['capability_version'],
            'source':'demo','mode':'free','target':None})
        self.assertEqual(response.status_code,201,response.json);sid=response.json['data']['session_id']
        self.fx.store.set_state(self.fx.user,sid,'running',s.new_id());self.fx.finish(sid)
        result=self.fx.client.get('/api/v1/records/'+sid).json['data']
        self.assertEqual(result['confirmed_demo_findings'][0]['end_ms'],item['duration_ms'])
        self.assertEqual(result['posture_observations'],[])
        builder=AIContextBuilder(self.fx.app)
        bundle=builder.build(self.fx.user,self.fx.auth,{'context_kind':'record','context_id':sid,'history_scope':'none','text':'动作错误了嘛'})
        issue=next(v for v in bundle['facts']['evidence'] if v.get('source')=='user_confirmed_demo_annotation')
        self.assertFalse(issue['model_detected']);self.assertEqual(issue['start_ms'],2000)
        self.assertTrue(builder.current(self.fx.user,self.fx.auth,bundle['reference']))
        with patch('fitness_app.presentation_assets.confirmed_demo_findings',return_value=[]):
            self.assertFalse(builder.current(self.fx.user,self.fx.auth,bundle['reference']))


    def setUp(self):
        self.fx=shared.RecordTests();self.fx.setUp()

    def tearDown(self):
        for key in ('video_jobs','session_manager','preview_manager','media_inputs'):self.fx.app.extensions[key].stop()
        self.fx.tearDown()

    def test_home_range_poster_catalogue_and_demo_selector(self):
        anon=self.fx.app.test_client();item=demo_resource()
        self.assertIn('user-1694',anon.get('/').text)
        self.assertIn('高位下拉',anon.get('/').text)
        self.assertNotIn('内置演示',anon.get('/').text)
        self.assertNotIn('演示视频',anon.get('/').text)
        self.assertNotIn('DVIDS / Barbell Bent Over Row',anon.get('/').text)
        with anon.get(item['video_url'],headers={'Range':'bytes=0-99'}) as response:
            self.assertEqual(response.status_code,206);self.assertEqual(len(response.data),100)
        with anon.get(item['poster_url']) as response:self.assertEqual(response.mimetype,'image/jpeg')
        self.assertEqual(anon.get('/static/demo/user-9250/track').status_code,404)
        items=self.fx.client.get('/api/v1/demos').json['data']['demos']
        self.assertEqual([x['demo_id'] for x in items],['user-1575','user-1694','user-1688','user-1689'])
        for hidden in ('user-1692','user-1693'):self.assertNotIn(hidden,anon.get('/').text)
        self.assertTrue(all(x['source_type']=='user_supplied' for x in items))
        self.assertNotIn('E:\\08_Downloads',json.dumps(items))
        self.assertIn('video-demo-select',self.fx.client.get('/video?demo=1').text)
        self.assertIn('video-demo-choice',self.fx.client.get('/video').text)
        for number in ('1692','1693','1694'):
            page=self.fx.client.get('/video?demo=1&demo_id=user-'+number)
            self.assertEqual(page.status_code,200 if number=='1694' else 404)
            if number=='1694':self.assertIn('user-'+number,page.text)
            item=demo_resource('user-'+number)
            response=anon.get(item['track_url'])
            self.assertEqual(response.json['source_sha256'],item['video']['sha256']);response.close()
        self.assertEqual(self.fx.client.get('/video?demo=1&demo_id=not-registered').status_code,404)
        for key in ('user-9248','user-9250'):
            archived=demo_resource(key,require_track=False)
            self.assertEqual(archived['action_id'],'cable_face_pull')
            self.assertEqual(archived['title'],'绳索面拉')
        self.assertEqual(items[0]['title'],'蝴蝶机夹胸')
        self.assertEqual(self.fx.store.list_records(self.fx.user)['total'],0)

    def test_annotation_http_cache_and_original_model_results(self):
        client=self.fx.app.test_client();item=demo_resource('user-1694')
        response=client.get(item['track_url'])
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.get_etag()[0],item['track_etag'])
        self.assertTrue(all('model_feedback' in f for f in response.json['frames']))
        self.assertIn(response.json['frames'][0]['feedback']['state'],('observing','unassessable'))
        self.assertTrue(any(f['feedback']['state']=='issue' for f in response.json['frames']))
        self.assertEqual(client.get(item['track_url'],headers={'If-None-Match':response.headers['ETag']}).status_code,304)
        normal=client.get(demo_resource('user-1575')['track_url'])
        self.assertNotIn('display_annotation',normal.json)
        self.assertTrue(any(f['feedback']['state']=='clear' for f in normal.json['frames']))
        normal.close()

    def test_real_1575_full_video_analysis_and_statistics_isolation(self):
        fx=self.fx;inputs=fx.app.extensions['media_inputs'];jobs=fx.app.extensions['video_jobs']
        response=fx.post(fx.client,'/api/v1/demo-inputs',{'demo_id':'user-1575'})
        self.assertEqual(response.status_code,202,response.json)
        iid=response.json['data']['input_id'];inputs.threads[iid].join(15)
        self.assertEqual(inputs.snapshot(fx.user,iid)['status'],'ready')
        action=get_action('pec_deck_fly')
        config={'action_id':action['action_id'],'capability_version':action['capability_version'],
                'source':'demo','mode':'free','target':None,'input_id':iid,'demo_id':'user-1575','coaching_view':'auto'}
        response=fx.post(fx.client,'/api/v1/sessions',config)
        self.assertEqual(response.status_code,201,response.json)
        sid=response.json['data']['session_id']
        response=fx.post(fx.client,'/api/v1/jobs/video',{'session_id':sid,'expected_version':0})
        self.assertEqual(response.status_code,202,response.json)
        jid=response.json['data']['job_id'];jobs.runs[jid].thread.join(90)
        self.assertFalse(jobs.runs[jid].thread.is_alive(),'整段真实分析超时，不能当完成')
        data=jobs.snapshot(fx.user,jid)
        self.assertEqual(data['status'],'completed',data)
        record=fx.store.snapshot(fx.user,sid,True)
        self.assertEqual(record['source'],'demo')
        self.assertEqual(fx.store.list_records(fx.user)['total'],0)
        self.assertFalse(inputs._path(iid).exists())
        self.assertTrue(any(x['status']=='not_observed' for x in record['posture_observations']))
        for code in ('pec_deck_fly.trunk_sway','pec_deck_fly.arm_asymmetry'):
            self.assertTrue(any(x['code']==code and x['status']=='not_observed' for x in record['posture_observations']))
        self.assertFalse(any(x['status']=='issue' for x in record['posture_observations']))
        self.assertFalse(any(x.get('reason')=='view_mismatch' for x in record['posture_observations']))
        restored=fx.client.get('/api/v1/records/'+sid+'/demo-source')
        self.assertEqual(restored.status_code,200)
        self.assertEqual(restored.json['data']['source_type'],'user_supplied')
        report={'source_id':'user-1575','source_sha256':demo_resource('user-1575')['video']['sha256'],
                'status':data['status'],'rule_version':record['posture_policy_version'],
                'observations':len(record['posture_observations']),
                'status_counts':{status:sum(x['status']==status for x in record['posture_observations']) for status in ('issue','not_observed','unassessable')},
                'excluded_from_personal_statistics':True,'temporary_input_cleaned':True,
                'independent_error_truth':False,'paid_ai_calls':0,'classification_training_runs':0}
        atomic_json(ROOT/'data/verification/round3/user-demo-analysis.json',report)


if __name__=='__main__':unittest.main()
