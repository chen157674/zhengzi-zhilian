"""视频父任务和单段修订接口；仍兼容原固定动作视频API。"""
from flask import request,jsonify,render_template,redirect
from flask_login import current_user
from sqlalchemy import select
from . import db_schema as s
from .auth import APIError,body
from .records import uuid_id
from .video_analyses import VideoAnalyses
from .catalogue import definitions


def register_video_analyses(app):
    manager=VideoAnalyses(app);app.extensions['video_analyses']=manager
    app.extensions['auth_revoke_hooks'].append(manager.revoke)
    app.extensions['start_hooks'].append(manager.recover)
    app.extensions['stop_hooks'].insert(0,manager.stop)

    @app.get('/video/analyses/<pid>')
    def video_analysis_page(pid):
        try:manager.snapshot(current_user.user_id,current_user.id,uuid_id(pid))
        except APIError as error:
            if error.code=='NOT_FOUND':return redirect('/video?analysis-unavailable=1',303)
            raise
        return render_template('video_auto.html',active='video',actions=definitions(),analysis_id=pid,action=None)

    @app.post('/api/v1/video-analyses')
    def video_analysis_create():
        data=body({'input_id','mode','action_id','coaching_view','save_skeleton'})
        return jsonify(data=manager.start(current_user.user_id,current_user.id,data,request.headers.get('Idempotency-Key'))),202

    @app.get('/api/v1/video-analyses')
    def video_analysis_list():
        if set(request.args)-{'active'} or request.args.get('active') not in (None,'true','false'):raise APIError('INVALID_REQUEST','筛选字段无效',400)
        conditions=[s.video_analyses.c.user_id==current_user.user_id]
        if request.args.get('active')=='true':conditions.append(s.video_analyses.c.status.not_in({'completed','cancelled','interrupted','failed'}))
        with manager.db.transaction(False) as c:
            ids=c.execute(select(s.video_analyses.c.id).where(*conditions).order_by(s.video_analyses.c.created_at_ms.desc(),s.video_analyses.c.id).limit(20)).scalars().all()
        return jsonify(data={'analyses':[manager.snapshot(current_user.user_id,current_user.id,pid) for pid in ids],'limit':20})

    @app.get('/api/v1/video-analyses/<pid>')
    def video_analysis_get(pid):return jsonify(data=manager.snapshot(current_user.user_id,current_user.id,uuid_id(pid)))

    @app.get('/api/v1/video-analyses/<pid>/delete-options')
    def video_analysis_delete_options(pid):
        if request.args:raise APIError('INVALID_REQUEST','删除范围不接受额外筛选',400)
        return jsonify(data=manager.deletion_options(current_user.user_id,current_user.id,uuid_id(pid)))

    @app.delete('/api/v1/video-analyses/<pid>')
    def video_analysis_delete(pid):
        data=body({'expected_version','expected_inventory','confirm'})
        return jsonify(data=manager.delete(current_user.user_id,current_user.id,uuid_id(pid),data,request.headers.get('Idempotency-Key')))

    @app.post('/api/v1/video-analyses/<pid>/cancel')
    def video_analysis_cancel(pid):
        data=body({'expected_version'})
        return jsonify(data=manager.cancel(current_user.user_id,current_user.id,uuid_id(pid),data.get('expected_version'),request.headers.get('Idempotency-Key'))),202

    @app.post('/api/v1/video-segments/<segment_id>/revisions')
    def video_segment_revise(segment_id):
        data=body({'input_id','action_id','expected_version','coaching_view','save_skeleton'})
        return jsonify(data=manager.reanalyse(current_user.user_id,current_user.id,uuid_id(segment_id),data,request.headers.get('Idempotency-Key'))),202

    @app.get('/api/v1/video-revisions/<rid>')
    def video_revision_get(rid):return jsonify(data=manager.revision(current_user.user_id,current_user.id,uuid_id(rid)))

    @app.post('/api/v1/video-revisions/<rid>/cancel')
    def video_revision_cancel(rid):
        data=body({'expected_version'})
        return jsonify(data=manager.cancel_revision(current_user.user_id,current_user.id,uuid_id(rid),data.get('expected_version'))),202

    @app.post('/api/v1/video-analyses/<pid>/cleanup')
    def video_analysis_cleanup(pid):
        body(set());return jsonify(data=manager.cleanup(current_user.user_id,current_user.id,uuid_id(pid)))

    @app.post('/api/v1/video-revisions/<rid>/cleanup')
    def video_revision_cleanup(rid):
        body(set());return jsonify(data=manager.cleanup(current_user.user_id,current_user.id,uuid_id(rid),True))

    @app.post('/api/v1/video-analyses/<pid>/retry-finalize')
    def video_analysis_finalize(pid):
        data=body({'expected_version'});return jsonify(data=manager.finalize(current_user.user_id,current_user.id,uuid_id(pid),data.get('expected_version')))

    @app.post('/api/v1/video-revisions/<rid>/retry-finalize')
    def video_revision_finalize(rid):
        data=body({'expected_version'});return jsonify(data=manager.finalize(current_user.user_id,current_user.id,uuid_id(rid),data.get('expected_version'),True))
