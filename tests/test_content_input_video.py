"""未启用候选输入策略的短视频接续：实际解码／Full／参考周期／保存，不重跑旧业务矩阵。"""
import hashlib
import json
import unittest
from unittest.mock import patch
from fitness_app.paths import ROOT
from fitness_app import db_schema as s
from fitness_app.catalogue import get_action
from fitness_app.pose_input import ContentPoseService
import test_formal_records as shared


class ContentVideoTests(unittest.TestCase):
    def test_actual_full_video_uses_recorded_policy_and_saves_reference_cycles(self):
        fx=shared.RecordTests();create=shared.create_app
        with patch.object(shared,'create_app',lambda config:create({**config,'PREVIEW_POSE_FACTORY':ContentPoseService})):
            fx.setUp()
        try:
            media=fx.app.extensions['media_inputs'];jobs=fx.app.extensions['video_jobs']
            self.assertIs(jobs.pose_factory,ContentPoseService)
            self.assertIs(fx.app.extensions['preview_manager'].pose_factory,ContentPoseService)
            source=ROOT/'data/datasets/development-movements/jumping-jacks-commons-segment.mp4'
            source_hash=hashlib.sha256(source.read_bytes()).hexdigest()
            with source.open('rb') as stream:uploaded=media.upload(fx.user,fx.auth,stream,s.new_id())
            iid=uploaded['input_id'];media.threads[iid].join(10)
            self.assertEqual(media.snapshot(fx.user,iid)['status'],'ready')
            created=fx.post(fx.client,'/api/v1/sessions',{'action_id':'jumping_jack','capability_version':get_action('jumping_jack')['capability_version'],
                'source':'video','mode':'free','target':None,'input_id':iid})
            self.assertEqual(created.status_code,201,created.json);sid=created.json['data']['session_id']
            response=fx.post(fx.client,'/api/v1/jobs/video',{'session_id':sid,'expected_version':0})
            self.assertEqual(response.status_code,202,response.json);jid=response.json['data']['job_id']
            jobs.runs[jid].thread.join(30);self.assertFalse(jobs.runs[jid].thread.is_alive())
            job=jobs.snapshot(fx.user,jid);self.assertEqual(job['status'],'completed',job)
            record=fx.store.snapshot(fx.user,sid,True)
            output=ROOT/'data/verification/step-19/content-input-video.json'
            history=json.loads(output.read_text(encoding='utf-8')) if output.exists() else {'schema_version':1,'runs':[]}
            data={'checks':'observed_before_assertions','scope':'explicit candidate factory; short registered public H264 source, actual Full; development-reference check, not independent accuracy',
                'source_sha256':source_hash,'frames':jobs.runs[jid].frames,'expected_count':3,'count':record['total_count'],'input_policy':record['input_policy'],
                'source_dimensions':[640,480],'source_kept':source.exists(),'owned_upload_removed':not media._path(iid).exists(),
                'code_sha256':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in (
                    'fitness_app/pose_input.py','fitness_app/preview_routes.py','fitness_app/video_jobs.py','fitness_app/records.py')}}
            history['runs'].append(data)
            output.write_text(json.dumps(history,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
            try:
                self.assertEqual(record['status'],'completed');self.assertEqual(record['total_count'],3)
                self.assertEqual(record['input_policy']['version'],'content-roi-2')
                self.assertEqual(record['source_sha256'],source_hash)
                self.assertEqual([e['event_seq'] for e in record['events']],[1,2,3])
                self.assertTrue(data['owned_upload_removed']);self.assertTrue(data['source_kept'])
                data['checks']='passed'
            except Exception as error:
                data['checks']='failed';data['error']=str(error);raise
            finally:
                output.write_text(json.dumps(history,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

        finally:
            for name in ('video_jobs','session_manager','preview_manager','media_inputs'):fx.app.extensions[name].stop()
            fx.tearDown()


if __name__=='__main__':unittest.main()
