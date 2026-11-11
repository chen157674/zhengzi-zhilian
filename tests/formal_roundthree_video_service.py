"""第三轮上传UI隔离服务；明确合成姿态/分类，不作为真实健身识别证据。"""
import os,sys,json
from pathlib import Path
from fitness_app.paths import ROOT,configure_environment
configure_environment();sys.path.insert(0,str(ROOT/'tests'))
from fitness_app import app as app_module
from fitness_app.auth import initialize_admin
from fitness_app.runtime import serve
from flask import jsonify,request
from fitness_app.database import StorageError
import test_offline_video as offline
import test_video_analyses as fake
import numpy as np

assert Path(os.environ['FITNESS_DATABASE_PATH']).parent.resolve()==ROOT/'.tmp/tests/round3-video-ui'
original=app_module.create_app
bootstrap=original({'TESTING':True,'DATABASE_PATH':os.environ['FITNESS_DATABASE_PATH']})
initialize_admin(bootstrap.extensions['database'],'Upload_Admin','Round3-upload-fixture-2026');bootstrap.extensions['database'].dispose()
poses=[];reads=[];predictor=fake.Predictor();control={}
original_predict=predictor.predict
def predict(*args,**kwargs):
    result=original_predict(*args,**kwargs)
    if control.get('unknown'):result['action_id']=None
    return result
predictor.predict=predict
def pose_factory(**kwargs):
    pose=offline.Pose();poses.append(pose);return pose
def frames(path,info,*,start_ms=0,end_ms=None):
    reads.append((start_ms,end_ms))
    for stamp in range(start_ms,end_ms or info['duration_ms'],40):yield stamp,np.full((48,64,3),180,np.uint8)
def testing_app(config):
    app=original({**config,'TESTING':True,'DATABASE_PATH':os.environ['FITNESS_DATABASE_PATH'],'PREVIEW_POSE_FACTORY':pose_factory,
        'VIDEO_ANALYSIS_PREDICTOR':lambda:predictor,'VIDEO_ANALYSIS_FRAMES':frames,'SESSION_ENGINE_PROVIDER':lambda action:fake.Engine(action,control)})
    @app.get('/api/v1/_fixture/video-stats')
    def statistics():return jsonify(data={'pose_passes':len(poses),'pose_calls':sum(p.calls for p in poses),'predictor_calls':predictor.calls,'ranges':reads})
    old=app.extensions['video_analyses'].store.finish
    def finish(*args,**kwargs):
        if control.get('terminal_fault'):raise StorageError('UI terminal metadata fixture')
        return old(*args,**kwargs)
    app.extensions['video_analyses'].store.finish=finish
    @app.post('/api/v1/_fixture/video-control')
    def configure():
        data=request.get_json();control['terminal_fault']=data['terminal_fault'];control['unknown']=data.get('unknown',False)
        return jsonify(data={'configured':True})
    return app
app_module.create_app=testing_app
serve(0)
