"""摄像头会话的控制、心跳和帧 API。"""
import json
import time
from flask import request,jsonify,render_template
from flask_login import current_user
from sqlalchemy import select

from .auth import APIError,body
from .db_schema import now_ms,action_events
from .frame_pipeline import MAX_JPEG_BYTES
from .records import uuid_id,integer
from .sessions import SessionManager
from .catalogue import get_action
from .posture_engine import create_analysis_engine as create_counter


def read_frame():
    if set(request.form)!={'metadata'} or set(request.files)!={'frame'}:raise APIError('INVALID_REQUEST','需要一个 JPEG 和 metadata 字段')
    if len(request.form['metadata'])>2048:raise APIError('INVALID_REQUEST','帧元数据过长')
    try:metadata=json.loads(request.form['metadata'])
    except (ValueError,TypeError) as error:raise APIError('INVALID_REQUEST','帧元数据必须是 JSON 对象') from error
    image=request.files['frame']
    if image.mimetype!='image/jpeg':raise APIError('INVALID_FRAME','仅接受 JPEG',422)
    return metadata,image.read(MAX_JPEG_BYTES+1)


def register_sessions(app,db):
    testing=app.config.get('TESTING')
    provider=app.config.get('SESSION_ENGINE_PROVIDER',create_counter) if testing else create_counter
    manager=SessionManager(app.extensions['session_store'],app.extensions['persistence'],app.extensions['preview_manager'],
        app.config.get('MONOTONIC_CLOCK') or time.monotonic,app.config.get('CLOCK') or now_ms,
        engine_provider=provider,allow_observer=bool(testing and app.config.get('ALLOW_POSE_ONLY_SESSIONS')))
    app.extensions['session_manager']=manager
    app.extensions['auth_revoke_hooks'].append(manager.revoke)
    app.extensions['start_hooks'].append(manager.start)
    app.extensions['stop_hooks'].insert(0,manager.stop)

    @app.post('/api/v1/sessions/<session_id>/control')
    def session_control(session_id):
        payload=body({'page_id','takeover','expected_version'})
        return jsonify(data=manager.claim(current_user.user_id,current_user.id,current_user.expires_at_ms,uuid_id(session_id),
            uuid_id(request.headers.get('Idempotency-Key')),payload))

    @app.post('/api/v1/sessions/<session_id>/heartbeat')
    def session_heartbeat(session_id):
        return jsonify(data=manager.heartbeat(current_user.user_id,current_user.id,uuid_id(session_id),body({'control_token','controller_epoch','stream_epoch'})))

    @app.post('/api/v1/sessions/<session_id>/frames')
    def session_frame(session_id):
        metadata,jpeg=read_frame()
        return jsonify(data=manager.submit_frame(current_user.user_id,current_user.id,uuid_id(session_id),request.headers.get('X-Control-Token'),metadata,jpeg)),202

    @app.get('/api/v1/sessions/<session_id>/updates')
    def session_updates(session_id):
        sid=uuid_id(session_id)
        if set(request.args)-{'after_event_seq','limit','frame_seq'}:raise APIError('INVALID_REQUEST','更新查询字段无效')
        try:after=int(request.args.get('after_event_seq','0'));limit=int(request.args.get('limit','100'))
        except ValueError as error:raise APIError('VALIDATION_FAILED','分页格式无效',422) from error
        integer(after,'after_event_seq');integer(limit,'limit',1,200)
        snapshot=manager.decorate_snapshot(current_user.user_id,app.extensions['persistence'].snapshot(current_user.user_id,sid))
        with db.transaction(False) as c:
            events=[dict(row) for row in c.execute(select(action_events).where(action_events.c.session_id==sid,action_events.c.event_seq>after)
                .order_by(action_events.c.event_seq).limit(limit+1)).mappings()]
        has_more=len(events)>limit;events=events[:limit]
        for event in events:event['assessments']=json.loads(event.pop('assessment_json'))
        snapshot.update(events=events,next_after_event_seq=events[-1]['event_seq'] if events else after,has_more=has_more)
        return jsonify(data=snapshot)

    @app.get('/realtime/<session_id>')
    def realtime_page(session_id):
        sid=uuid_id(session_id);snapshot=app.extensions['session_store'].snapshot(current_user.user_id,sid)
        if snapshot['source']!='camera':raise APIError('INVALID_STATE','该记录不是摄像头分析',409)
        action={**get_action(snapshot['action_id']),'name':snapshot['action_name'],'capability_version':snapshot['capability_version']}
        return render_template('realtime.html',active='realtime',session_id=sid,action=action,allow_observer=manager.allow_observer)
