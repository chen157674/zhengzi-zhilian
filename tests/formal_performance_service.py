"""19：仅将已登记动作素材送入隔离浏览器；正式 Full 与计时逻辑保持原样。"""
import os
import json
import hashlib
from pathlib import Path
from flask import send_file
from fitness_app import app as app_module
from fitness_app.paths import ROOT, project_path
from fitness_app.runtime import serve
from fitness_app.pose import PoseService

action=os.environ.get('FITNESS_PERFORMANCE_ACTION','forearm_plank')
expected_root={'conventional_deadlift':'ui-step19-deadlift','reverse_lunge':'ui-step19-lunge','bodyweight_squat':'ui-step19-squat'}.get(action,'ui-step19-performance')
if action in {'standard_push_up','standing_dumbbell_curl'}:expected_root='ui-step19-push-up' if action=='standard_push_up' else 'ui-step19-curl'
if action=='forearm_plank' and os.environ.get('FITNESS_PERFORMANCE_CURRENT')=='1':expected_root='ui-step19-current-plank'
if action=='forearm_plank' and os.environ.get('FITNESS_PERFORMANCE_POSTURE')=='1':expected_root='ui-step19-posture-plank'
assert Path(os.environ['FITNESS_DATABASE_PATH']).parent.resolve() == ROOT/'.tmp/tests'/expected_root
if action in {'standard_push_up','standing_dumbbell_curl'}:
    from fitness_app.research_data import load_dataset
    fixed={'standard_push_up':'seven-exercises/push_up/good/subject_001_push_up_good_side.mp4',
           'standing_dumbbell_curl':'seven-exercises/bicep_curl/good/subject_002_bicep_curl_good_diagonal.mp4'}
    sample=next(s for s in load_dataset() if s.path==fixed[action])
    assert sample.partition=='train' and sample.action_id==action
    selected=sample.key
    source={'id':selected,'path':sample.absolute_path.relative_to(ROOT).as_posix(),'sha256':sample.sha256}
elif action=='conventional_deadlift':
    from fitness_app.research_data import load_dataset
    sample=next(s for s in load_dataset() if s.path=='mydeadlift/Gerakan Benar/GB_S02_R29.mp4')
    assert sample.partition in {'train','development'} and sample.action_id==action
    selected='mydeadlift-dev-gb-s02-r29'
    source={'id':selected,'path':sample.absolute_path.relative_to(ROOT).as_posix(),'sha256':sample.sha256}
else:
    selected = {'forearm_plank': 'dvids-640237-side', 'jumping_jack': 'commons-jumping-jacks-segment', 'reverse_lunge': 'dvids-551833', 'bodyweight_squat': 'dvids-548443'}[action]
    source = next(s for s in json.loads((ROOT/'data/datasets/development-movements/sources.json').read_text(encoding='utf-8'))['sources'] if s['id'] == selected)
video = project_path(source['path'])
assert hashlib.sha256(video.read_bytes()).hexdigest() == source['sha256']
original = app_module.create_app


def testing_app(config):
    settings = {**config, 'TESTING': True, 'DATABASE_PATH': os.environ['FITNESS_DATABASE_PATH']}
    if os.environ.get('FITNESS_JACK_STATELESS') == '1':
        assert selected == 'commons-jumping-jacks-segment'
        class StatelessCandidate:
            def __init__(self, **kwargs):
                self.pose = PoseService(video=False)
            def detect(self, rgb, stamp):
                return self.pose.detect(rgb, stamp)
            def close(self):
                self.pose.close()
        settings['PREVIEW_POSE_FACTORY'] = StatelessCandidate
    if os.environ.get('FITNESS_JACK_CONTENT_ROI') == '1':
        assert selected == 'commons-jumping-jacks-segment'
        from fitness_app.pose_input import ContentPoseService
        settings['PREVIEW_POSE_FACTORY'] = ContentPoseService
    capture_basic=os.environ.get('FITNESS_CAPTURE_BASIC_AMBIGUITY')=='1'
    if os.environ.get('FITNESS_CAPTURE_JACK_AMBIGUITY') == '1' or capture_basic:
        assert (capture_basic and action=='standard_push_up') or selected == 'commons-jumping-jacks-segment'
        from fitness_app.pose_input import ContentPoseService
        class CaptureFirstAmbiguity:
            def __init__(self, **kwargs):
                self.pose = (ContentPoseService if capture_basic else PoseService)(**kwargs)
            def detect(self, rgb, stamp):
                result = self.pose.detect(rgb, stamp)
                target = ROOT/'data/verification/step-19'/('push-up-ambiguous-frame.json' if capture_basic else 'jack-ambiguous-frame.json')
                if len(result['poses']) > 1 and not target.exists():
                    from PIL import Image
                    image = target.with_suffix('.png')
                    assert not image.exists(), '已有图片无来源记录，先核查，不能覆盖'
                    Image.fromarray(rgb).save(image)
                    target.write_text(json.dumps({'scope':'first multiple-pose output on the registered public source; raw decoded JPEG, no output filtering',
                        'source_id':source['id'],'source_url':source.get('source_url','https://data.mendeley.com/datasets/kgbb3yn47p/1'),'source_sha256':source['sha256'],
                        'author':source.get('author'),'license':source.get('copyright_status','CC BY 4.0'),'license_url':source.get('terms_url','https://creativecommons.org/licenses/by/4.0/'),
                        'capture_timestamp_ms':stamp,'model':result['model'],'width':rgb.shape[1],'height':rgb.shape[0],
                        'probe_source_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'poses':result['poses'],
                        'input_transform':result.get('input_transform'),'model_policy':'ContentPoseService wrapper for diagnostic only' if capture_basic else 'PoseService diagnostic'},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
                return result
            def close(self):
                self.pose.close()
        settings['PREVIEW_POSE_FACTORY'] = CaptureFirstAmbiguity
    app = original(settings)

    @app.get('/__fixture/performance-source')
    def source_video():
        return send_file(video, conditional=True)

    return app


app_module.create_app = testing_app
serve(0)
