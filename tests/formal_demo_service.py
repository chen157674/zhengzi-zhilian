"""18-C隔离服务：包装真实Full计数调用次数，不替换模型输出或读取预处理轨迹。"""
import os
from pathlib import Path
from flask import jsonify
from fitness_app import app as app_module
from fitness_app.auth import initialize_admin
from fitness_app.pose import PoseService
from fitness_app.paths import ROOT
from fitness_app.runtime import serve

assert Path(os.environ['FITNESS_DATABASE_PATH']).parent.resolve()==ROOT/'.tmp/tests/ui-step18-demo'
original=app_module.create_app
bootstrap=original({'TESTING':True,'DATABASE_PATH':os.environ['FITNESS_DATABASE_PATH']})
db=bootstrap.extensions['database'];initialize_admin(db,'Demo_Admin','demo-fixture-password-123');db.dispose()
metrics={'pose_calls':0,'model_instances':0}


class ActualPose:
    def __init__(self,**kwargs):
        self.model=PoseService(**kwargs);metrics['model_instances']+=1
    def detect(self,rgb,stamp):
        result=self.model.detect(rgb,stamp);metrics['pose_calls']+=1;return result
    def close(self):self.model.close()


def testing_app(config):
    app=original({**config,'TESTING':True,'DATABASE_PATH':os.environ['FITNESS_DATABASE_PATH'],'PREVIEW_POSE_FACTORY':ActualPose})
    @app.get('/__fixture/demo-metrics')
    def counters():return jsonify(data=metrics)
    return app


app_module.create_app=testing_app
serve(0)
