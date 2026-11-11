"""外部下载失败不得返回伪成功；仅使用E盘隔离清单和网络替身，不请求站点。"""
import contextlib,hashlib,io,json,tempfile,unittest
from pathlib import Path
from unittest.mock import MagicMock,patch
import yt_dlp
from fitness_app.paths import ROOT,project_path
from scripts.prepare_cc_exercise_videos import run,outcome_exit_status


class CCDownloadOutcomeTests(unittest.TestCase):
    def setUp(self):
        base=project_path(ROOT/'.tmp/tests');self.temp=tempfile.TemporaryDirectory(prefix='cc-download-',dir=base);self.directory=project_path(self.temp.name)
        self.manifest=self.directory/'sources.json';self.manifest.write_text(json.dumps({'sources':[]}),encoding='utf-8')
        self.network=MagicMock();self.downloader=self.network.return_value.__enter__.return_value
        self.stdout=contextlib.redirect_stdout(io.StringIO());self.stdout.__enter__();self.mock=patch('yt_dlp.YoutubeDL',self.network);self.mock.start()
    def tearDown(self):self.mock.stop();self.stdout.__exit__(None,None,None);self.temp.cleanup()
    def call(self,selected=None,**kwargs):return run(selected=selected,directory=self.directory,**kwargs)
    def register(self,payload,exists=True):
        path=self.directory/'youtube-fixture.mp4'
        if exists:path.write_bytes(payload)
        source={'id':'youtube-fixture','path':path.relative_to(ROOT).as_posix(),'sha256':hashlib.sha256(payload).hexdigest(),'bytes':len(payload),'partition':'reserved_holdout'}
        self.manifest.write_text(json.dumps({'sources':[source]}),encoding='utf-8');return path
    def test_empty_selection_does_not_fall_back_to_old_full_download_list(self):
        results=self.call([]);self.assertEqual(results,[]);self.assertEqual(outcome_exit_status(results),2);self.network.assert_not_called()
    def test_authentication_failure_is_structured_and_nonzero_without_manifest_changes(self):
        before=self.manifest.read_bytes();self.downloader.extract_info.side_effect=yt_dlp.utils.DownloadError('isolated authentication required')
        results=self.call([('fixture','dumbbell_bench_press')]);self.assertEqual(outcome_exit_status(results),1);self.assertEqual(results[0]['status'],'failed')
        self.assertEqual(self.manifest.read_bytes(),before);self.downloader.process_info.assert_not_called()
    def test_missing_or_changed_registered_file_is_not_a_successful_reuse(self):
        path=self.register(b'expected',False);results=self.call([('fixture','dumbbell_bench_press')]);self.assertEqual(outcome_exit_status(results),1)
        path.write_bytes(b'changed');results=self.call([('fixture','dumbbell_bench_press')]);self.assertEqual(outcome_exit_status(results),1);self.assertEqual(path.read_bytes(),b'changed');self.network.assert_not_called()
    def test_verified_reuse_keeps_stricter_reserved_role_and_avoids_network(self):
        self.register(b'expected');before=self.manifest.read_bytes();results=self.call([('fixture','dumbbell_bench_press')],policies={'fixture':{'prospective_role':'train'}})
        self.assertEqual(outcome_exit_status(results),0);self.assertEqual(results[0]['partition'],'reserved_holdout');self.assertEqual(self.manifest.read_bytes(),before);self.network.assert_not_called()
    def test_unlicensed_or_unexpected_publisher_never_starts_media_download(self):
        for info,policies,status in [({'license':'Standard YouTube License'},{},'rejected_license'),({'license':None},{},'rejected_license'),
            ({'license':'Creative Commons Attribution license (reuse allowed)','channel_id':'other'},{'fixture':{'expected_channel_id':'expected'}},'failed'),
            ({'license':'Creative Commons Attribution license (reuse allowed)'},{'fixture':{'prospective_role':'unrecognized'}},'failed')]:
            self.downloader.extract_info.return_value=info
            result=self.call([('fixture','dumbbell_bench_press')],policies=policies);self.assertEqual(result[0]['status'],status);self.assertEqual(outcome_exit_status(result),1)
        self.downloader.process_info.assert_not_called()
    def test_new_download_applies_reserved_role_before_registering_source(self):
        target=self.directory/'youtube-fixture.mp4';payload=b'fixture-only-network-bytes'*500
        self.downloader.extract_info.return_value={'license':'Creative Commons Attribution license (reuse allowed)','channel_id':'expected','title':'Synthetic transport fixture','uploader':'fixture'}
        self.downloader.prepare_filename.return_value=str(target);self.downloader.process_info.side_effect=lambda info:target.write_bytes(payload)
        result=self.call([('fixture','dumbbell_bench_press')],policies={'fixture':{'expected_channel_id':'expected','prospective_role':'reserved_new_publisher_holdout'}})
        self.assertEqual(outcome_exit_status(result),0);row=json.loads(self.manifest.read_text(encoding='utf-8'))['sources'][0]
        self.assertEqual(row['partition'],'reserved_holdout');self.assertTrue(row['evaluation_only']);self.assertFalse(row['model_inference_allowed_before_freeze'])
    def test_old_publisher_role_conflict_or_missing_identity_is_rejected_before_video_bytes(self):
        before=self.manifest.read_bytes()
        for existing,role in [({'validation'},'train'),({'train'},'validation_supplement'),({'validation'},'reserved_new_publisher_holdout'),({'train'},'reserved_new_publisher_holdout')]:
            self.downloader.extract_info.return_value={'license':'Creative Commons Attribution license (reuse allowed)','channel_id':'known','title':'fixture'}
            result=self.call([('fixture','seated_cable_row')],policies={'fixture':{'prospective_role':role}},partition_roles={'youtube-channel:known':existing})
            self.assertEqual(outcome_exit_status(result),1);self.assertEqual(self.manifest.read_bytes(),before)
        self.downloader.extract_info.return_value={'license':'Creative Commons Attribution license (reuse allowed)','title':'missing-channel fixture'}
        result=self.call([('fixture','seated_cable_row')],policies={'fixture':{'prospective_role':'train'}},partition_roles={})
        self.assertEqual(outcome_exit_status(result),1);self.downloader.process_info.assert_not_called()
    def test_same_batch_new_publisher_cannot_cross_roles_after_first_registration(self):
        target=self.directory/'youtube-first.mp4';payload=b'fixture-only-network-bytes'*500;roles={}
        self.downloader.extract_info.return_value={'license':'Creative Commons Attribution license (reuse allowed)','channel_id':'new-fixture-channel','title':'fixture','uploader':'fixture'}
        self.downloader.prepare_filename.return_value=str(target);self.downloader.process_info.side_effect=lambda info:target.write_bytes(payload)
        result=self.call([('first','seated_cable_row'),('second','seated_cable_row')],policies={'first':{'prospective_role':'train'},'second':{'prospective_role':'validation_supplement'}},partition_roles=roles)
        self.assertEqual([r['status'] for r in result],['downloaded','failed']);self.assertEqual(self.downloader.process_info.call_count,1)
        self.assertEqual(len(json.loads(self.manifest.read_text(encoding='utf-8'))['sources']),1);self.assertEqual(roles,{})
    def test_locked_failed_partial_is_reported_without_aborting_other_sources(self):
        first=self.directory/'youtube-first.mp4';locked=first.with_suffix('.mp4.part');second=self.directory/'youtube-second.mp4';payload=b'fixture-video-only'*1000
        self.downloader.extract_info.return_value={'license':'Creative Commons Attribution license (reuse allowed)','channel_id':'fixture','title':'fixture','uploader':'fixture'}
        self.downloader.prepare_filename.side_effect=[str(first),str(second)];count=0
        def transfer(info):
            nonlocal count
            count+=1
            if count==1:
                locked.write_bytes(b'incomplete');raise yt_dlp.utils.DownloadError('fixture transport failure')
            second.write_bytes(payload)
        self.downloader.process_info.side_effect=transfer;original_unlink=Path.unlink
        def unlink(path,*args,**kwargs):
            if path==locked:raise PermissionError('fixture owned file still open')
            return original_unlink(path,*args,**kwargs)
        with patch.object(Path,'unlink',unlink):result=self.call([('first','seated_cable_row'),('second','seated_cable_row')])
        self.assertEqual([r['status'] for r in result],['failed','downloaded']);self.assertEqual(outcome_exit_status(result),1)
        self.assertEqual(result[0]['cleanup_pending'][0]['path'],locked.relative_to(ROOT).as_posix());self.assertEqual(result[0]['cleanup_pending'][0]['error'],'PermissionError')
        self.assertTrue(locked.exists());self.assertEqual(second.read_bytes(),payload);self.assertEqual(len(json.loads(self.manifest.read_text(encoding='utf-8'))['sources']),1)


if __name__=='__main__':unittest.main()
