"""10 的隔离浏览器服务；D-POSE／D-EVENT 不用于识别准确性。"""
import os
import time
import sys
from fitness_app import app as app_module
from fitness_app.auth import initialize_admin,password_hash
from fitness_app.runtime import serve
from fitness_app.paths import ROOT
from fitness_app import db_schema as s
sys.path.insert(0,str(ROOT/'tests'))
from test_formal_video_jobs import SyntheticPose,Events

from pathlib import Path
assert Path(os.environ['FITNESS_DATABASE_PATH']).parent.resolve()==ROOT/'.tmp/tests/ui-step10'
original=app_module.create_app
bootstrap=original({'TESTING':True,'DATABASE_PATH':os.environ['FITNESS_DATABASE_PATH']})
db=bootstrap.extensions['database'];initialize_admin(db,'Video_Admin','video-fixture-password-123')
with db.transaction() as c:c.execute(s.users.insert().values(username_display='Video_User',username_key='video_user',password_hash=password_hash('video-fixture-password-123')))
db.dispose()

class SlowPose(SyntheticPose):
    def detect(self,rgb,stamp):
        time.sleep(.08)
        return super().detect(rgb,stamp)

def testing_app(config):
    return original({**config,'TESTING':True,'DATABASE_PATH':os.environ['FITNESS_DATABASE_PATH'],
        'PREVIEW_POSE_FACTORY':SlowPose,'SESSION_ENGINE_PROVIDER':lambda action:Events(),'ALLOW_POSE_ONLY_SESSIONS':True})

app_module.create_app=testing_app
serve(0)
