"""显式开启的本机预训练参考接口；沿用现有鉴权/CSRF，不写个人训练记录。"""
import logging
import math
import threading
from flask import jsonify
from .auth import APIError,body
from .catalogue import get_action
from .pretrained_resources import registry
from .pretrained_policy import enabled

LOG=logging.getLogger(__name__)


def register_pretrained(app):
    slot=threading.BoundedSemaphore(1);pool={}
    app.extensions['pretrained_services']=pool
    app.extensions.setdefault('stop_hooks',[]).append(pool.clear)

    def get_model(key):
        if key not in pool:
            if key=='reference':
                from .reference_similarity import ReferenceSimilarity
                factory=app.config.get('PRETRAINED_REFERENCE_FACTORY',ReferenceSimilarity) if app.config.get('TESTING') else ReferenceSimilarity
                pool[key]=factory()
            else:
                from .exercise_feedback import ExerciseFeedbackModel
                factory=app.config.get('PRETRAINED_EXERCISE_FACTORY',ExerciseFeedbackModel) if app.config.get('TESTING') else ExerciseFeedbackModel
                pool[key]=factory(key)
        return pool[key]

    def run(key,callback):
        if not slot.acquire(blocking=False):raise APIError('MODEL_BUSY','本机参考模型正在处理，请稍后重试',429)
        try:return callback(get_model(key))
        except (ValueError,TypeError,OverflowError) as error:raise APIError('INVALID_MODEL_INPUT',str(error),422) from error
        except (OSError,KeyError,StopIteration) as error:
            LOG.exception('预训练参考资源不可用')
            raise APIError('MODEL_UNAVAILABLE','本机参考模型尚未准备完成',503) from error
        finally:slot.release()

    @app.get('/api/v1/correction-models')
    def pretrained_capabilities():
        resources=registry()
        return jsonify(data={'frame_observations_enabled':enabled(),'local_only':True,
            'exercise_classifier_actions':['barbell_bench_press','conventional_deadlift','bodyweight_squat'],
            'reference_comparison':{'requires_same_action':True,'dimensions':23,'angle_type':'sagittal','overall_correctness':False},
            'vision_models':{name:{'ready':record.get('state')=='ready','runtime_validated':record.get('runtime_validated',False)}
                             for name,record in resources.get('models',{}).items()},
            'independent_accuracy_validated':False})

    @app.post('/api/v1/correction-models/pose-check')
    def pretrained_pose_check():
        data=body({'action_id','landmarks'})
        from .exercise_feedback import ACTIONS
        action=data.get('action_id');points=data.get('landmarks')
        if not isinstance(action,str) or action not in ACTIONS:raise APIError('ACTION_UNSUPPORTED','该预训练分类器仅支持杠铃卧推、硬拉和深蹲',422)
        if not isinstance(points,list) or len(points)!=33:raise APIError('INVALID_MODEL_INPUT','需要33个姿态点',422)
        for point in points:
            if not isinstance(point,dict) or set(point)-{'x','y','z','visibility','presence','valid'}:
                raise APIError('INVALID_MODEL_INPUT','姿态点结构无效',422)
            if any(type(point.get(key)) not in (int,float) or not math.isfinite(point[key]) for key in ('x','y','z','visibility')):
                raise APIError('INVALID_MODEL_INPUT','姿态点须包含有限坐标与可见性',422)
            if not 0<=point['visibility']<=1:raise APIError('INVALID_MODEL_INPUT','可见性须在0到1之间',422)
        return jsonify(data=run(action,lambda model:model.observe(points)))

    @app.post('/api/v1/correction-models/reference-compare')
    def pretrained_reference_compare():
        data=body({'action_id','reference_action_id','reference_angles','observed_angles'},max_bytes=384*1024)
        action=data.get('action_id');reference_action=data.get('reference_action_id')
        if not isinstance(action,str) or not isinstance(reference_action,str) or not get_action(action) or not get_action(reference_action):raise APIError('ACTION_UNSUPPORTED','参考动作或观察动作无效',422)
        for key,maximum in (('reference_angles',800),('observed_angles',300)):
            values=data.get(key)
            if not isinstance(values,list) or not 1<=len(values)<=maximum:raise APIError('INVALID_MODEL_INPUT','角度序列长度无效',422)
            if any(not isinstance(row,list) or len(row)!=23 or any(type(v) not in (int,float) or not math.isfinite(v) or abs(v)>360 for v in row) for row in values):
                raise APIError('INVALID_MODEL_INPUT','角度序列须为有限23维数组，范围不超过360度',422)
        if action!=reference_action:
            return jsonify(data={'status':'unassessable','reason':'reference_action_mismatch','correctness':None})
        result=run('reference',lambda model:model.compare(data['reference_angles'],data['observed_angles'],action,reference_action))
        return jsonify(data=result)
