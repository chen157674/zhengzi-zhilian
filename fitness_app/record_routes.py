"""个人记录 API 和页面，保存入口只供服务端工作线程调用。"""
from flask import jsonify,render_template,request,g
from flask_login import current_user
import time

from .auth import APIError,body
from .catalogue import definitions
from .pose import model_status
from .records import SessionStore,uuid_id,integer
from .persistence import PersistenceCoordinator
from .db_schema import now_ms


def register_records(app,db):
    fault=app.config.get('PERSISTENCE_FAULT') if app.config.get('TESTING') else None
    from .pose_input import input_policy_snapshot
    from .gym_pose import GymPoseService as ContentPoseService
    factory=app.config.get('PREVIEW_POSE_FACTORY',ContentPoseService) if app.config.get('TESTING') else ContentPoseService
    store=SessionStore(db,app.config.get('CLOCK') or now_ms,fault,input_policy_snapshot(factory))
    writer=PersistenceCoordinator(store,app.config.get('MONOTONIC_CLOCK') or time.monotonic)
    app.extensions['session_store']=store;app.extensions['persistence']=writer
    def deletion_complete(uid,ids):
        writer.forget(uid,ids)
        manager=app.extensions.get('session_manager')
        if manager:manager.forget_records(uid,ids)
        ai=app.extensions.get('ai_jobs')
        if ai:ai.invalidate_source_cache(uid,ids)
    app.extensions['record_deletion_complete']=deletion_complete
    app.extensions.setdefault('start_hooks',[]).extend([store.recover_interrupted,writer.start])
    app.extensions.setdefault('stop_hooks',[]).append(writer.stop)

    @app.get('/api/v1/posture/rules')
    def posture_rules():
        from .posture_rules import RULES,rules_for,policy_version,SOURCES,PARAMETER_SOURCE
        return jsonify(data={'version':policy_version(),'evidence_level':'reference','parameter_source':PARAMETER_SOURCE,
                             'sources':SOURCES,'muscle_force_assessment':False,
                             'actions':{action:rules_for(action) for action in sorted({x[0] for x in RULES.values()})}})

    @app.get('/api/v1/records/<session_id>/posture')
    def posture_record(session_id):
        data=store.snapshot(current_user.user_id,uuid_id(session_id),include_details=True)
        return jsonify(data={'session_id':data['session_id'],'action_id':data['action_id'],
                             'counting_available':data['counting_available'],'count':data['total_count'] if data['counting_available'] else None,
                             'coaching_view':data['config'].get('coaching_view','auto'),'policy_version':data['posture_policy_version'],
                             'time_base':'source_ms' if data['source'] in {'video','demo'} else 'session_elapsed_ms',
                             **data['posture']})

    @app.post('/api/v1/sessions')
    def create_session():
        config=body({'action_id','capability_version','source','mode','target','input_id','demo_id','coaching_view','save_skeleton'})
        if writer.unhealthy:raise APIError('STORAGE_UNAVAILABLE','尚有保存故障，请先处理原会话',503)
        if not model_status()['available']:raise APIError('MODEL_UNAVAILABLE','姿态资源尚未就绪',503)
        key=uuid_id(request.headers.get('Idempotency-Key'));manager=app.extensions.get('session_manager')
        if manager:manager.guard_create(current_user.user_id,key)
        data=store.create(current_user.user_id,current_user.id,config,key,stability=config.get('source')=='camera')
        writer.remember(current_user.user_id,data)
        manager=app.extensions.get('session_manager')
        if manager:data=manager.decorate_snapshot(current_user.user_id,data)
        return jsonify(data=data),201

    @app.get('/api/v1/sessions/<session_id>')
    def get_session(session_id):
        data=writer.snapshot(current_user.user_id,uuid_id(session_id))
        manager=app.extensions.get('session_manager')
        if manager:data=manager.decorate_snapshot(current_user.user_id,data)
        return jsonify(data=data)

    @app.post('/api/v1/sessions/<session_id>/commands')
    def session_command(session_id):
        sid=uuid_id(session_id)
        data=body({'command','expected_version','confirm','last_committed_version','control_token','controller_epoch','stream_epoch','preview_id','preview_token'})
        command_id=uuid_id(request.headers.get('Idempotency-Key'))
        command=data.get('command')
        if not isinstance(command,str):raise APIError('VALIDATION_FAILED','会话命令无效',422)
        manager=app.extensions.get('session_manager')
        if getattr(g,'cleanup_identity',None):
            return jsonify(data=manager.cleanup_command(g.cleanup_identity['auth_id'],sid,command_id,data))
        if manager and command=='discard_unsaved' and 'control_token' in data:
            writer.snapshot(current_user.user_id,sid)
            return jsonify(data=manager.cleanup_command(current_user.id,sid,command_id,data))
        if command not in {'retry_save','discard_unsaved'}:
            manager=app.extensions.get('session_manager')
            if manager:return jsonify(data=manager.command(current_user.user_id,current_user.id,sid,command_id,data))
            raise APIError('INVALID_STATE','会话控制尚未开放',409)
        integer(data.get('expected_version'),'expected_version')
        preview=app.extensions.get('preview_manager')
        automatic=preview.record_failure(current_user.user_id,sid) if preview else None
        if automatic:
            original=store.snapshot(current_user.user_id,sid)
            if original['state_version']!=data['expected_version']:raise APIError('VERSION_CONFLICT','记录已更新，请重新读取',409)
            if command=='discard_unsaved' and (data.get('confirm') is not True or data.get('last_committed_version')!=original['state_version']):
                raise APIError('VALIDATION_FAILED','请确认已保存版本及放弃未保存内容',422)
            result=preview.recover_training(current_user.user_id,current_user.id,automatic['preview_id'],command=='discard_unsaved')
            snapshot=store.snapshot(current_user.user_id,sid)
            snapshot.update(save_state=result['save_state'],analysis_frozen=result['save_state']!='clean',unsaved_discarded=command=='discard_unsaved',terminal_persisted=snapshot['status'] in {'ended_early','interrupted','completed'})
            return jsonify(data=snapshot)
        if set(data)-{'command','expected_version','confirm','last_committed_version'}:
            raise APIError('INVALID_REQUEST','保存恢复命令不接受运动控制字段')
        if command=='discard_unsaved':
            if data.get('confirm') is not True:raise APIError('VALIDATION_FAILED','请明确确认放弃未保存部分',422)
            integer(data.get('last_committed_version'),'last_committed_version')
        manager=app.extensions.get('session_manager')
        original=writer.snapshot(current_user.user_id,sid)
        video_source=original['source'] in {'video','demo'}
        if video_source and app.extensions.get('video_jobs'):manager=app.extensions['video_jobs']
        result=(manager.recovery(current_user.user_id,current_user.id,sid,command_id,data) if video_source else manager.recover_command(current_user.user_id,current_user.id,sid,command_id,data)) if manager else writer.recovery_command(current_user.user_id,sid,current_user.id,command_id,data)
        return jsonify(data=result)

    @app.get('/api/v1/records')
    def list_records():
        allowed={'page','page_size','action_id','source','date_from','date_to','search'}
        if set(request.args)-allowed:raise APIError('INVALID_REQUEST','记录筛选字段不合法')
        try:page=int(request.args.get('page','1'));size=int(request.args.get('page_size','20'))
        except ValueError as error:raise APIError('VALIDATION_FAILED','分页格式不正确',422) from error
        result=store.list_records(current_user.user_id,page,size,**{key:request.args.get(key) for key in ('action_id','source','date_from','date_to','search')})
        result['records']=[writer.snapshot(current_user.user_id,item['session_id']) for item in result['records']]
        return jsonify(data=result)

    @app.get('/api/v1/records/<session_id>')
    def record_detail(session_id):
        sid=uuid_id(session_id)
        data=store.snapshot(current_user.user_id,sid,include_details=True)
        data.update({key:value for key,value in writer.snapshot(current_user.user_id,sid).items() if key not in {'events','intervals','posture_observations','posture'}})
        preview=app.extensions.get('preview_manager')
        failure=preview.record_failure(current_user.user_id,sid) if preview else None
        if failure:data.update(save_state='save_failed',analysis_frozen=True,recovery_preview_id=failure['preview_id'])
        data['retest']=app.extensions['retests'].summary(current_user.user_id,data)
        data['saved_skeleton']=app.extensions['skeleton_store'].summary(current_user.user_id,sid)
        data['movement_stability']=app.extensions['stability_store'].safe_snapshot(current_user.user_id,sid)
        return jsonify(data=data)

    @app.post('/api/v1/records/delete')
    def delete_records():
        data=body({'ids','expected_versions'})
        command=uuid_id(request.headers.get('Idempotency-Key'))
        with writer.deletion_guard(current_user.user_id,data.get('ids')):
            result=store.delete_records(current_user.user_id,current_user.id,data.get('ids'),data.get('expected_versions'),command)
        deletion_complete(current_user.user_id,result['deleted_ids'])
        return jsonify(data=result)

    @app.get('/records')
    def records_page():
        template='records.html' if request.args.get('view')=='list' or any(k in request.args for k in ('page','action_id','source','search','discarded','period')) else 'training_overview.html'
        return render_template(template,active='records',actions=definitions())

    @app.get('/records/<session_id>')
    def detail_page(session_id):
        store.snapshot(current_user.user_id,uuid_id(session_id))
        return render_template('record_detail.html',active='records',session_id=session_id)
