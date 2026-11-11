"""图像边界 UI 夹具：实际裁切原图与先前真实 SDK 输出，不重复模型推理。"""
import os
from io import BytesIO
from pathlib import Path
import sys

import av
from flask import send_file
from PIL import Image
from fitness_app import app as app_module
from fitness_app.auth import initialize_admin
from fitness_app.paths import ROOT
from fitness_app.runtime import serve

assert Path(os.environ['FITNESS_DATABASE_PATH']).parent.resolve()==ROOT/'.tmp/tests/ui-step14-bounds'
sys.path.insert(0,str(ROOT/'scripts'))
from prepare_movement_input import cached_frames
raw=cached_frames('dvids-692514-side',apply_bounds=False)[0]['framing']['landmarks']
with av.open(str(ROOT/'data/datasets/development-movements/overhead-press-dvids-692514-side.mp4')) as container:frame=next(container.decode(video=0)).to_image()
output=BytesIO();frame.save(output,format='JPEG',quality=80);jpeg=output.getvalue()


class CachedPose:
    def __init__(self,**unused):pass
    def detect(self,rgb,stamp):return {'poses':[raw],'model':'mediapipe-full-float16-1','content_bounds':[0,0,1,1]}
    def close(self):pass


original=app_module.create_app
bootstrap=original({'TESTING':True,'DATABASE_PATH':os.environ['FITNESS_DATABASE_PATH']})
db=bootstrap.extensions['database'];initialize_admin(db,'Bounds_Admin','bounds-fixture-password-123');db.dispose()


def testing_app(config):
    app=original({**config,'TESTING':True,'DATABASE_PATH':os.environ['FITNESS_DATABASE_PATH'],'PREVIEW_POSE_FACTORY':CachedPose})
    @app.get('/__fixture/content-bounds.jpg')
    def source():return send_file(BytesIO(jpeg),mimetype='image/jpeg')
    return app


app_module.create_app=testing_app
serve(0)
