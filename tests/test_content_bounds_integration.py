"""真实裁切反例经两个正式帧入口；复用原 SDK 输出，验证新 RGB 边界接线。"""
from io import BytesIO
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest

import av
from PIL import Image
import test_formal_records as shared
from fitness_app import db_schema as s
from fitness_app.paths import ROOT
from fitness_app.catalogue import get_action
from fitness_app.content_bounds import point_in_content
from fitness_app.counting_resources import DEFAULT_CONFIG,read_counting_manifest

sys.path.insert(0,str(ROOT/'scripts'))
from prepare_movement_input import cached_frames


class ImageBoundsConnections(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frames=cached_frames('dvids-692514-side',apply_bounds=False)
        cls.source=ROOT/'data/datasets/development-movements/overhead-press-dvids-692514-side.mp4'
        with av.open(str(cls.source)) as container:rgb=next(container.decode(video=0)).to_ndarray(format='rgb24')
        image=BytesIO();Image.fromarray(rgb).save(image,format='JPEG',quality=80);cls.jpeg=image.getvalue()

    def setUp(self):
        self.fx=shared.RecordTests();self.fx.setUp();self.preview=self.fx.app.extensions['preview_manager'];self.video=self.fx.app.extensions['video_jobs'];self.media=self.fx.app.extensions['media_inputs']

    def tearDown(self):
        self.video.stop();self.fx.app.extensions['session_manager'].stop();self.preview.stop();self.media.stop();self.fx.tearDown()

    @staticmethod
    def raw_pose(frame):
        return {'poses':[frame['framing']['landmarks']],'model':'mediapipe-full-float16-1','content_bounds':[0,0,1,1]}

    def test_preview_jpeg_uses_actual_pixels_and_masks_high_confidence_padding(self):
        frame=self.frames[0]
        class CachedPose:
            def __init__(inner,**unused):pass
            def detect(inner,rgb,stamp):return self.raw_pose(frame)
            def close(inner):pass
        self.preview.pose_factory=CachedPose
        created=self.fx.post(self.fx.client,'/api/v1/previews',{'action_id':'barbell_overhead_press','capability_version':get_action('barbell_overhead_press')['capability_version'],'page_id':s.new_id()})
        self.assertEqual(created.status_code,201,created.json);data=created.json['data'];pid=data['preview_id']
        token=self.fx.client.get('/api/v1/auth/csrf').json['data']['csrf_token']
        metadata={'stream_epoch':data['stream_epoch'],'frame_seq':0,'captured_ms':0,'width':324,'height':480}
        response=self.fx.client.post(f'/api/v1/previews/{pid}/frames',data={'metadata':json.dumps(metadata),'frame':(BytesIO(self.jpeg),'frame.jpg','image/jpeg')},headers={'X-CSRFToken':token,'X-Preview-Token':data['preview_token']})
        self.assertEqual(response.status_code,202,response.json)
        result=None
        for unused in range(100):
            result=self.preview.snapshot(self.fx.user,pid)['result']
            if result:break
            time.sleep(.01)
        self.assertIsNotNone(result);quality=result['framing'];self.assertFalse(quality['ready_for_tracking']);self.assertEqual(quality['code'],'MISSING_PARTS')
        self.assertFalse(quality['landmarks'][31]['valid']);self.assertIn(31,quality['outside_content_indices'])
        self.assertGreater(frame['framing']['landmarks'][31]['visibility'],.9,'原模型置信值未被改写')
        self.assertNotEqual(quality['content_bounds'],[0,0,1,1],'不能信任模型夹具里伪称的全图边界')

    def test_video_decoder_applies_same_mask_before_calling_any_adapter(self):
        frames={f['captured_ms']:f for f in self.frames};received=[]
        class CachedPose:
            def __init__(inner,**unused):pass
            def detect(inner,rgb,stamp):return self.raw_pose(frames[stamp])
            def close(inner):pass
        class Probe:
            def reset(inner,reason):pass
            def process(inner,frame,context):
                received.append(frame)
                return {'events':[]}
        self.video.pose_factory=CachedPose;self.video.engine_provider=lambda action:Probe()
        with self.source.open('rb') as stream:uploaded=self.media.upload(self.fx.user,self.fx.auth,stream,s.new_id())
        iid=uploaded['input_id'];self.media.threads[iid].join(10);self.assertEqual(self.media.snapshot(self.fx.user,iid)['status'],'ready')
        response=self.fx.post(self.fx.client,'/api/v1/sessions',{'action_id':'barbell_overhead_press','capability_version':get_action('barbell_overhead_press')['capability_version'],'source':'video','mode':'free','target':None,'input_id':iid})
        self.assertEqual(response.status_code,201,response.json);sid=response.json['data']['session_id']
        job=self.fx.post(self.fx.client,'/api/v1/jobs/video',{'session_id':sid,'expected_version':0});self.assertEqual(job.status_code,202,job.json)
        jid=job.json['data']['job_id'];self.video.runs[jid].thread.join(30);self.assertFalse(self.video.runs[jid].thread.is_alive())
        result=self.video.snapshot(self.fx.user,jid);self.assertEqual(result['status'],'completed',result)
        self.assertEqual(len(received),73,'此处只核边界过滤；不能把这73帧当作脚部真实可见')
        for frame in received:
            quality=frame['framing']
            self.assertTrue(all(not p['valid'] or point_in_content(p,quality['content_bounds']) for p in quality['landmarks']))
        self.assertFalse(self.media._path(iid).exists());self.assertEqual(result['session']['total_count'],0)

    def test_ready_resource_requires_the_new_input_validation_record(self):
        doc=json.loads(DEFAULT_CONFIG.read_text(encoding='utf-8'))
        self.assertEqual(doc['previous_capability_version'],'count-1-7aeed2550319')
        self.assertNotEqual(read_counting_manifest()['capability_version'],doc['previous_capability_version'])
        with tempfile.TemporaryDirectory(prefix='bounds-resource-',dir=ROOT/'.tmp/tests') as folder:
            path=Path(folder)/'resource.json';path.write_text(json.dumps({**doc,'input_validation_sha256':'0'*64}),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'输入边界核查摘要'):read_counting_manifest(path)


if __name__=='__main__':unittest.main()
