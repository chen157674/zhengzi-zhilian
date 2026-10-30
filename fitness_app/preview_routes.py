"""准备预览 API；图像仅在内存内处理，不保存为个人成果。"""
import json
import time
from flask import request,jsonify
from flask_login import current_user

from .auth import APIError,body
from .db_schema import now_ms
from .frame_pipeline import MAX_JPEG_BYTES
from .pose import model_status
from .gym_pose import GymPoseService as ContentPoseService
from .preview import PreviewManager
from .records import uuid_id
from .vision_slot import VisionSlot


def register_previews(app):
    slot=VisionSlot();app.extensions['vision_slot']=slot
    factory=app.config.get('PREVIEW_POSE_FACTORY',ContentPoseService) if app.config.get('TESTING') else ContentPoseService
    manager=PreviewManager(slot,factory,app.config.get('MONOTONIC_CLOCK') or time.monotonic,app.config.get('CLOCK') or now_ms,record_store=app.extensions['session_store'])
    app.extensions['preview_manager']=manager
    app.extensions['auth_revoke_hooks'].append(manager.revoke)
    app.extensions['stop_hooks'].append(manager.stop)

    @app.post('/api/v1/previews')
    def create_preview():
        data=body({'action_id','capability_version','page_id','recognition_mode','save_training','training_id','coaching_view','retest_origin','save_skeleton'})
        if app.extensions['persistence'].unhealthy:raise APIError('STORAGE_UNAVAILABLE','请先处理原会话保存故障',503)
        if not model_status(app.config.get('MODEL_PATH'))['available']:raise APIError('MODEL_UNAVAILABLE','姿态模型资源不可用',503)
        return jsonify(data=manager.create(current_user.user_id,current_user.id,current_user.expires_at_ms,data,request.headers.get('Idempotency-Key'))),201

    @app.get('/api/v1/framing-guide')
    def framing_guide_get():
        from .framing_guide import guide
        from .catalogue import get_action
        action=request.args.get('action_id')
        if set(request.args)-{'action_id'} or action and not get_action(action):raise APIError('VALIDATION_FAILED','动作不存在或取景参数无效',422)
        return jsonify(data=guide(action_id=action))

    @app.get('/api/v1/recognition')
    def recognition_status():
        from .semantic_recognition import create_recognizer
        recognizer=create_recognizer();recognition=recognizer.snapshot();validated=recognition['full_validation_passed']
        from .catalogue import definitions
        return jsonify(data={'default_mode':'auto','fallback_mode':'manual','recognition':recognition,
                             'catalogue_action_count':len(definitions()),'extended_action_validation_passed':validated,
                             'automatic_analysis_pipeline_available':True,
                             'automatic_analysis_available':recognizer.available,
                             'automatic_training_records_available':True,'full_twelve_action_support':recognizer.available,
                             'full_twelve_action_validation_passed':validated})

    @app.get('/api/v1/previews/<preview_id>')
    def get_preview(preview_id):return jsonify(data=manager.snapshot(current_user.user_id,uuid_id(preview_id)))

    @app.post('/api/v1/previews/<preview_id>/save-recovery')
    def recover_training(preview_id):
        data=body({'preview_token','stream_epoch','discard','owner_recovery'})
        if not isinstance(data.get('discard',False),bool):raise APIError('VALIDATION_FAILED','放弃选项无效',422)
        with manager.lock:
            if data.get('owner_recovery') is True:
                item=manager._find(current_user.user_id,uuid_id(preview_id))
                if not item.get('recorder'):raise APIError('INVALID_STATE','没有训练保存状态',409)
                if not item['recorder'].failed:return jsonify(data=item['recorder'].snapshot())
            else:item=manager._authorize(current_user.user_id,uuid_id(preview_id),data.get('preview_token'),data.get('stream_epoch'),False)
            if not item.get('recorder'):raise APIError('INVALID_STATE','此预览没有训练记录',409)
            result=manager.recover_training(current_user.user_id,current_user.id,uuid_id(preview_id),data.get('discard',False))
        return jsonify(data=result)

    @app.get('/api/v1/automatic-training/pending')
    def pending_training_saves():
        return jsonify(data={'pending':manager.failed_trainings(current_user.user_id)})

    @app.delete('/api/v1/previews/<preview_id>')
    def release_preview(preview_id):
        data=body({'preview_token','stream_epoch'})
        return jsonify(data=manager.release(current_user.user_id,uuid_id(preview_id),data,request.headers.get('Idempotency-Key')))

    @app.post('/api/v1/previews/<preview_id>/frames')
    def preview_frame(preview_id):
        if set(request.form)!={'metadata'} or set(request.files)!={'frame'}:raise APIError('INVALID_REQUEST','需要一个 JPEG 和 metadata 字段')
        text=request.form['metadata']
        if len(text)>2048:raise APIError('INVALID_REQUEST','帧元数据过长')
        try:metadata=json.loads(text)
        except (ValueError,TypeError) as error:raise APIError('INVALID_REQUEST','帧元数据必须是 JSON 对象') from error
        frame=request.files['frame']
        if frame.mimetype!='image/jpeg':raise APIError('INVALID_FRAME','仅接受 JPEG',422)
        jpeg=frame.read(MAX_JPEG_BYTES+1)
        return jsonify(data=manager.submit(current_user.user_id,uuid_id(preview_id),request.headers.get('X-Preview-Token'),metadata,jpeg)),202
