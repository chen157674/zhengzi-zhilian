"""09 的 D-POSE／D-EVENT／D-CLOCK 独立业务夹具，非实际动作模型。"""
import json
import os
import time
from pathlib import Path
from fitness_app import app as app_module
from fitness_app.auth import initialize_admin,password_hash
from fitness_app.paths import ROOT
from fitness_app.runtime import serve
from fitness_app import db_schema as s

temporary=Path(os.environ['FITNESS_DATABASE_PATH']).parent.resolve()
assert temporary==ROOT/'.tmp/tests/ui-step09'
original=app_module.create_app
bootstrap=original({'TESTING':True,'DATABASE_PATH':os.environ['FITNESS_DATABASE_PATH']})
db=bootstrap.extensions['database'];initialize_admin(db,'Target_Admin','target-fixture-password-123')
with db.transaction() as c:c.execute(s.users.insert().values(username_display='Target_User',username_key='target_user',password_hash=password_hash('target-fixture-password-123')))
db.dispose()


def clock():
    path=temporary/'clock.json'
    return time.monotonic()+(json.loads(path.read_text())['offset'] if path.exists() else 0)


class SyntheticPose:
    def __init__(self,**unused):pass
    def detect(self,rgb,stamp):
        points=[dict(x=.5,y=.2,z=0,visibility=.95,presence=.95) for unused in range(33)]
        for index,xy in {11:(.4,.3),12:(.6,.3),13:(.35,.4),14:(.65,.4),15:(.3,.5),16:(.7,.5),23:(.4,.6),24:(.6,.6),25:(.4,.75),26:(.6,.75),27:(.4,.9),28:(.6,.9)}.items():points[index].update(x=xy[0],y=xy[1])
        return {'poses':[points],'model':'D-POSE-target-test'}
    def close(self):pass


class PrescribedEvents:
    def __init__(self):self.groups=set()
    def reset(self,reason):pass
    def process(self,frame,context):
        group=context['set_index']
        if group in self.groups:return {}
        self.groups.add(group);end=frame['captured_ms']
        return {'events':[dict(side='none',start_ms=max(0,end-100),end_ms=end,algorithm_version='D-EVENT-target-test',feature_schema_version='D-POSE-target-test',assessments=[])]}


def testing_app(config):
    return original({**config,'TESTING':True,'DATABASE_PATH':os.environ['FITNESS_DATABASE_PATH'],'ALLOW_POSE_ONLY_SESSIONS':True,
        'PREVIEW_POSE_FACTORY':SyntheticPose,'SESSION_ENGINE_PROVIDER':lambda action:PrescribedEvents(),'MONOTONIC_CLOCK':clock})


app_module.create_app=testing_app
serve(0)
