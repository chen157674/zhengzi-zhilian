"""10-A 新增媒体边界；不重复 SDK 安装、姿态或认证完整矩阵。"""
import io
import time
import unittest
from types import SimpleNamespace

import test_formal_records as shared
from fitness_app.media import copy_input,probe_video,video_frames,source_stamp
from fitness_app.catalogue import get_action
from fitness_app.auth import APIError
from fitness_app import db_schema as s


def sample_mp4(rotation=0,codec='libx264',frames=4):
    import av
    import numpy as np
    data=io.BytesIO()
    with av.open(data,'w',format='mp4') as container:
        stream=container.add_stream(codec,rate=10);stream.width=64;stream.height=48;stream.pix_fmt='yuv420p'
        if rotation:stream.set_display_rotation(rotation)
        for index in range(frames):
            pixels=np.zeros((48,64,3),dtype=np.uint8);pixels[:24,:32,0]=255;pixels[24:,32:,1]=255
            frame=av.VideoFrame.from_ndarray(pixels,format='rgb24');frame.pts=index
            for packet in stream.encode(frame):container.mux(packet)
        for packet in stream.encode():container.mux(packet)
    return data.getvalue()


class MediaTests(unittest.TestCase):
    def setUp(self):
        self.fixture=shared.RecordTests();self.fixture.setUp()
        self.client=self.fixture.client;self.manager=self.fixture.app.extensions['media_inputs']

    def tearDown(self):
        self.manager.stop();self.fixture.tearDown()

    def upload(self,payload,key=None):
        token=self.client.get('/api/v1/auth/csrf').json['data']['csrf_token']
        return self.client.post('/api/v1/inputs',data={'file':(io.BytesIO(payload),'arbitrary.name')},headers={'X-CSRFToken':token,'Idempotency-Key':key or s.new_id()})

    def settled(self,input_id):
        thread=self.manager.threads.get(input_id)
        if thread:thread.join(5)
        self.assertFalse(thread and thread.is_alive(),'验证应结束')
        return self.client.get('/api/v1/inputs/'+input_id).json['data']

    def test_reader_limits_and_source_time(self):
        target=io.BytesIO();self.assertEqual(copy_input(io.BytesIO(b'abcdefgh'),target,8)[0],8)
        with self.assertRaises(APIError) as caught:copy_input(io.BytesIO(b'abcdefghi'),io.BytesIO(),8)
        self.assertEqual(caught.exception.code,'INPUT_TOO_LARGE')
        with self.assertRaises(APIError):copy_input(io.BytesIO(),io.BytesIO(),8)
        self.assertEqual(source_stamp(SimpleNamespace(pts=52,time_base=.1),5000),200)
        for frame in (SimpleNamespace(pts=None,time_base=.1),SimpleNamespace(pts=18001,time_base=.1)):
            with self.assertRaises(APIError):source_stamp(frame,0)

    def test_actual_codec_rotation_and_sequential_source_frames(self):
        for rotation in (0,90):
            path=self.manager.root.parent/('probe-'+str(rotation)+'.mp4');path.write_bytes(sample_mp4(rotation))
            try:
                info=probe_video(path);frames=list(video_frames(path,info))
                self.assertEqual([stamp for stamp,rgb in frames],[0,100,200,300]);self.assertEqual(info['duration_ms'],400)
                self.assertEqual((info['width'],info['height']),(64,48) if rotation==0 else (48,64))
                self.assertEqual(frames[0][1].shape[:2],(48,64) if rotation==0 else (64,48))
            finally:path.unlink()
        path=self.manager.root.parent/'unsupported.mp4';path.write_bytes(sample_mp4(codec='mpeg4'))
        try:
            with self.assertRaises(APIError) as caught:probe_video(path)
            self.assertEqual(caught.exception.code,'UNSUPPORTED_MEDIA')
        finally:path.unlink()

    def test_upload_digest_replay_owner_and_cleanup(self):
        payload=sample_mp4();key=s.new_id();response=self.upload(payload,key);self.assertEqual(response.status_code,202,response.json)
        input_id=response.json['data']['input_id'];data=self.settled(input_id)
        self.assertEqual(data['status'],'ready');self.assertEqual(data['bytes'],len(payload));self.assertEqual(len(data['sha256']),64)
        self.assertNotIn('owner_auth_session_id',data['encoding']);self.assertNotIn('temporary_relative_path',data)
        replay=self.upload(payload,key);self.assertEqual(replay.json['data']['input_id'],input_id)
        conflict=self.upload(payload+b'different-content',key);self.assertEqual(conflict.status_code,409)
        other=self.fixture.app.test_client();self.fixture.login(other,'Other_User')
        self.assertEqual(other.get('/api/v1/inputs/'+input_id).status_code,404)
        config=dict(action_id='bodyweight_squat',capability_version=get_action('bodyweight_squat')['capability_version'],source='video',mode='free',target=None,input_id=input_id)
        session=self.fixture.post(self.client,'/api/v1/sessions',config);self.assertEqual(session.status_code,201,session.json)
        bad={**config,'mode':'target','target':dict(sets=2,value=1,unit='reps',rest_seconds=30)}
        self.assertEqual(self.fixture.post(self.client,'/api/v1/sessions',bad).status_code,422)
        token=self.client.get('/api/v1/auth/csrf').json['data']['csrf_token']
        removed=self.client.delete('/api/v1/inputs/'+input_id,json={},headers={'X-CSRFToken':token,'Idempotency-Key':s.new_id()})
        self.assertEqual(removed.status_code,200,removed.json);self.assertEqual(removed.json['data']['status'],'cleaned')
        self.assertFalse(self.manager._path(input_id).exists());self.assertEqual(self.client.get('/api/v1/records').json['data']['total'],0)

    def test_rejected_media_is_removed_and_multiple_files_refused(self):
        response=self.upload(b'not a video');self.assertEqual(response.status_code,202,response.json)
        input_id=response.json['data']['input_id'];data=self.settled(input_id)
        self.assertEqual(data['status'],'rejected');self.assertEqual(data['encoding']['error_code'],'UNDECODABLE_MEDIA');self.assertFalse(self.manager._path(input_id).exists())
        token=self.client.get('/api/v1/auth/csrf').json['data']['csrf_token']
        multiple=self.client.post('/api/v1/inputs',data={'file':[(io.BytesIO(b'a'),'a.mp4'),(io.BytesIO(b'b'),'b.mp4')]},headers={'X-CSRFToken':token,'Idempotency-Key':s.new_id()})
        self.assertIn(multiple.status_code,(400,413));self.assertEqual(self.client.get('/api/v1/records').json['data']['total'],0)


if __name__=='__main__':unittest.main()
