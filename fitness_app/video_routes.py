"""本地视频任务 API；回看匹配只计算摘要，不启动视觉任务。"""
import hmac
import json
from .paths import project_path
from flask import request,jsonify,render_template
from flask_login import current_user
from sqlalchemy import select

from . import db_schema as s
from .auth import APIError,body
from .records import uuid_id
from .video_jobs import VideoJobs
from .media import copy_input
from .catalogue import definitions,get_action
from .posture_engine import create_analysis_engine as create_counter
from .presentation_assets import demo_resource,demo_catalogue
from .demo_result_summary import evidence_ranges,review_ranges,timecode


def register_video(app,db):
    testing=app.config.get('TESTING')
    options={'engine_provider':create_counter}
    if testing:
        for key,config in [('engine_provider','SESSION_ENGINE_PROVIDER'),('pose_factory','PREVIEW_POSE_FACTORY'),('frames','VIDEO_FRAME_READER')]:
            if config in app.config:options[key]=app.config[config]
        options['allow_observer']=bool(app.config.get('ALLOW_POSE_ONLY_SESSIONS'))
    manager=VideoJobs(app.extensions['session_store'],app.extensions['persistence'],app.extensions['media_inputs'],app.extensions['vision_slot'],**options)
    app.extensions['video_jobs']=manager
    app.extensions['auth_revoke_hooks'].append(manager.revoke)
    app.extensions['stop_hooks'].insert(0,manager.stop)

    @app.post('/api/v1/jobs/video')
    def video_start():
        data=body({'session_id','expected_version'})
        return jsonify(data=manager.start(current_user.user_id,current_user.id,uuid_id(data.get('session_id')),data.get('expected_version'),uuid_id(request.headers.get('Idempotency-Key')))),202

    @app.get('/api/v1/jobs/<job_id>')
    def video_job(job_id):
        jid=uuid_id(job_id)
        with db.transaction(False) as c:kind=c.scalar(select(s.jobs.c.kind).where(s.jobs.c.id==jid,s.jobs.c.owner_id==current_user.user_id))
        if kind!='video':raise APIError('NOT_FOUND','任务不存在',404)
        return jsonify(data=manager.snapshot(current_user.user_id,jid))

    @app.get('/api/v1/jobs')
    def current_jobs():
        with db.transaction(False) as c:
            ids=c.execute(select(s.jobs.c.id).where(s.jobs.c.owner_id==current_user.user_id,s.jobs.c.kind=='video',s.jobs.c.status.not_in({'completed','cancelled','interrupted','failed'})).order_by(s.jobs.c.created_at_ms.desc()).limit(5)).scalars().all()
        return jsonify(data={'jobs':[manager.snapshot(current_user.user_id,jid) for jid in ids]})

    @app.post('/api/v1/jobs/<job_id>/cancel')
    def video_cancel(job_id):
        data=body({'expected_version'})
        jid=uuid_id(job_id)
        with db.transaction(False) as c:kind=c.scalar(select(s.jobs.c.kind).where(s.jobs.c.id==jid,s.jobs.c.owner_id==current_user.user_id))
        if kind!='video':raise APIError('NOT_FOUND','任务不存在',404)
        return jsonify(data=manager.cancel(current_user.user_id,current_user.id,jid,data.get('expected_version'),uuid_id(request.headers.get('Idempotency-Key')))),202

    @app.post('/api/v1/records/<session_id>/source-match')
    def match_source(session_id):
        data=app.extensions['session_store'].snapshot(current_user.user_id,uuid_id(session_id))
        if data['source']!='video' or not data['source_sha256']:raise APIError('INVALID_STATE','该记录没有可匹配视频源',409)
        if request.form or set(request.files)!={'file'} or len(request.files.getlist('file'))!=1:raise APIError('INVALID_REQUEST','请选择一个原视频',400)
        class Sink:
            def write(self,chunk):return len(chunk)
        size,digest=copy_input(request.files['file'].stream,Sink())
        if size!=data['source_bytes'] or not hmac.compare_digest(digest,data['source_sha256']):raise APIError('SOURCE_MISMATCH','文件内容与本条记录不匹配，请重新选择原视频',422)
        return jsonify(data={'matched':True,'sha256':digest,'bytes':size,'skeleton_available':False})

    @app.get('/api/v1/records/<session_id>/demo-source')
    def record_demo_source(session_id):
        data=app.extensions['session_store'].snapshot(current_user.user_id,uuid_id(session_id))
        if data['source']!='demo':raise APIError('INVALID_STATE','该记录不是内置演示',409)
        item=demo_resource(data['config'].get('demo_id'),require_track=False)
        if not item or (item['video']['sha256'],item['video']['bytes'])!=(data['source_sha256'],data['source_bytes']):
            raise APIError('SOURCE_MISMATCH','登记演示原片缺失或与本条记录不匹配',422)
        return jsonify(data={'demo_id':item['id'],'video_url':item['video_url'],'title':item['title'],'skeleton_available':False,
            'source_type':item.get('source_type','public'),'source_label':item.get('source_label','DVIDS / Barbell Bent Over Row'),
            **{k:item[k] for k in ('credit','source_url','license','non_endorsement')}})

    @app.get('/video')
    def video_page():
        action=get_action(request.args.get('action',''))
        template='video.html' if request.args.get('demo')=='1' else 'video_auto.html'
        demos=demo_catalogue();requested=request.args.get('demo_id')
        selected=next((item for item in demos if item['id']==requested),None) if requested else (demos[0] if demos else None)
        if requested and not selected:raise APIError('DEMO_UNAVAILABLE','该演示暂不可用，请从视频分析页重新选择',404)
        return render_template(template,active='video',actions=definitions(),action=action,demo=selected,demos=demos,allow_observer=bool(testing and app.config.get('ALLOW_POSE_ONLY_SESSIONS')))

    @app.get('/video/examples/<demo_id>')
    def saved_example(demo_id):
        demo=demo_resource(demo_id)
        if not demo or demo_id not in {item['id'] for item in demo_catalogue()}:
            raise APIError('DEMO_UNAVAILABLE','该视频结果暂不可用，请重新选择',404)
        track=json.loads(project_path(demo['track']['path']).read_text(encoding='utf-8'))
        if track.get('source_sha256')!=demo['video']['sha256'] or track.get('action_id')!=demo['action_id'] or track.get('feedback_schema_version')!=1:
            raise APIError('RESULT_UNAVAILABLE','视频与已保存结果不匹配，请重新分析',409)
        # 原始逐帧检查保持独立；不将首页人工展示标注当成算法输出。
        frames=track.get('frames',[]);issues=evidence_ranges(track,demo['duration_ms']);reviews=review_ranges(demo)
        playlist=[{'id':demo['id'],'title':demo.get('display_title',demo['title']),'video_url':demo['video_url'],
                   'track_url':'/static/demo/'+demo['id']+'/analysis-track?v='+demo['track']['sha256'][:12],
                   'poster_url':demo['poster_url'],'source_sha':demo['video']['sha256']}]
        titles=list(dict.fromkeys(issue['title'] for issue in issues))
        assessed=any(check.get('status')=='not_observed' for frame in frames for check in frame.get('feedback',{}).get('checks',[]))
        conclusion={'state':'issue','title':'发现'+str(len(titles))+'项需要调整的动作细节','description':'；'.join(titles)} if titles else {
            'state':'clear' if assessed else 'unassessable',
            'title':'已检查项目未见明确偏差' if assessed else '请补充清晰完整的动作画面',
            'description':'可以回看各时段的动作观察。' if assessed else '请让关键部位连续入镜，并使用适合当前动作的机位。'}
        return render_template('saved_example.html',active='video',demo=demo,demo_playlist=playlist,issues=issues,
            conclusion=conclusion,reviews=reviews,timecode=timecode,
            initial_ms=reviews[0]['start_ms'] if reviews else issues[0]['seek_ms'] if issues else 0)

    @app.get('/video/jobs/<job_id>')
    def video_job_page(job_id):
        data=manager.snapshot(current_user.user_id,uuid_id(job_id))
        return render_template('video.html',active='video',actions=definitions(),action=get_action(data['session']['action_id']),job_id=data['job_id'],allow_observer=bool(testing and app.config.get('ALLOW_POSE_ONLY_SESSIONS')))
