"""Flask 应用适配层。路由／视频任务沿上游流程逐步迁移，不运行其全局相机。"""
from __future__ import annotations

import importlib.metadata
import secrets
import uuid
import os
from datetime import timedelta

from flask import Flask, jsonify, request, g, render_template
from werkzeug.exceptions import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from .paths import PROJECT_ID, ROOT, project_path
from .pose import model_status
from .database import Database, StorageError, ConflictError
from .db_schema import app_meta, SCHEMA_VERSION
from .auth import register_auth, APIError


def create_app(config: dict | None = None) -> Flask:
    app = Flask(__name__)
    from .media_routes import ProjectRequest
    app.request_class=ProjectRequest
    app.config.from_mapping(SECRET_KEY=secrets.token_hex(32), TRUSTED_HOSTS=["127.0.0.1", "localhost", "[::1]"],
        DATABASE_PATH=os.environ.get("FITNESS_DATABASE_PATH", str(ROOT/"data/app.sqlite3")),
        ALLOW_ROUND5_MIGRATION=os.environ.get('FITNESS_ALLOW_ROUND5_MIGRATION')=='1',
        CORRECTION_LABELS_ENABLED=os.environ.get('FITNESS_ENABLE_CORRECTION_LABELS')=='1',
        PRETRAINED_CORRECTION_ENABLED=os.environ.get('FITNESS_PRETRAINED_CORRECTION')=='1',
        SESSION_COOKIE_HTTPONLY=True,SESSION_COOKIE_SAMESITE="Strict",SESSION_COOKIE_SECURE=False,
        SESSION_REFRESH_EACH_REQUEST=False,PERMANENT_SESSION_LIFETIME=timedelta(hours=12))
    if config:
        app.config.update(config)
    from .mobile_origin import configure_mobile_origin
    configure_mobile_origin(app,app.config.get('MOBILE_PUBLIC_ORIGIN') or os.environ.get('FITNESS_PUBLIC_ORIGIN'))
    app.json.ensure_ascii = False
    # 基础单元测试可只创建资源切片；正式服务始终启用真实数据库与认证。
    data_enabled = not app.config["TESTING"] or bool(config and "DATABASE_PATH" in config)
    db = None
    if data_enabled:
        db = Database(app.config["DATABASE_PATH"])
        db.initialize(allow_round5_migration=bool(app.config.get('ALLOW_ROUND5_MIGRATION')))
        app.extensions["database"] = db
    if not app.config["TESTING"]:
        secret_path = project_path(app.config["DATABASE_PATH"]).parent / "auth-secret.key"
        if not secret_path.exists():
            try:
                with secret_path.open("xb") as stream:
                    stream.write(secrets.token_bytes(32))
            except FileExistsError:
                pass
        secret = secret_path.read_bytes()
        if len(secret)<32:
            raise StorageError("认证密钥无效，不自动覆盖")
        app.config["SECRET_KEY"] = secret

    @app.before_request
    def check_origin():
        g.request_id = str(uuid.uuid4())
        # 先触发受信 Host 校验，不能由后续匿名鉴权提前返回掩盖非法 Host。
        request.host
        if request.path.startswith('/api/v1/correction-models'):
            request.max_content_length=384*1024
        if request.method=='POST' and request.path=='/api/v1/account/avatar':
            from .avatar_upload import MAX_AVATAR_BYTES
            request.max_content_length=MAX_AVATAR_BYTES+64*1024
            request.max_form_memory_size=128*1024;request.max_form_parts=2
            if request.content_length is not None and request.content_length>request.max_content_length:
                raise APIError('AVATAR_TOO_LARGE','头像文件不能超过4 MiB',413)
        if request.method=='POST' and (request.path=='/api/v1/inputs' or request.path.startswith('/api/v1/records/') and request.path.endswith('/source-match')):
            from .media import MAX_BYTES
            request.max_content_length=MAX_BYTES+64*1024
            request.max_form_memory_size=128*1024;request.max_form_parts=1
            if request.content_length is not None and request.content_length>request.max_content_length:
                raise APIError('INPUT_TOO_LARGE','单个视频不能超过 1 GiB',413)
        if request.method=='POST' and request.path.startswith('/api/v1/sessions/') and request.path.endswith('/commands'):
            request.max_content_length=64*1024
        if request.method=='POST' and request.path.startswith('/api/v1/jobs/') and request.path.endswith('/cancel'):
            request.max_content_length=64*1024
        if request.method=='POST' and request.path.startswith('/api/v1/ai/conversations/') and request.path.endswith('/messages'):
            request.max_content_length=2*1024*1024
        origin = request.headers.get("Origin")
        if request.method not in {"GET", "HEAD", "OPTIONS"} and origin and origin != request.host_url.rstrip("/"):
            return jsonify(error={"code": "FORBIDDEN", "message": "请求来源不匹配", "fields": {}, "retryable": False},
                           request_id=g.request_id), 403
        if request.method=='POST' and request.path.endswith('/frames'):
            # 先于 CSRF 的 multipart 读取限流／限体积，避免预解析无界缓冲。
            request.max_content_length=2*1024*1024+64*1024
            request.max_form_memory_size=128*1024
            request.max_form_parts=2
            if request.content_length is not None and request.content_length>request.max_content_length:
                raise APIError('FRAME_TOO_LARGE','帧请求超过 JPEG 及必要元数据上限',413)
            parts=request.path.split('/')
            if len(parts)==6 and parts[1:4]==['api','v1','previews']:
                from flask_login import current_user
                if current_user.is_authenticated and not current_user.must_change_password:
                    from .records import uuid_id
                    g.preview_upload_release=app.extensions['preview_manager'].begin_upload(current_user.user_id,uuid_id(parts[4]),request.headers.get('X-Preview-Token'))
            elif len(parts)==6 and parts[1:4]==['api','v1','sessions']:
                from flask_login import current_user
                if current_user.is_authenticated and not current_user.must_change_password:
                    from .records import uuid_id
                    g.preview_upload_release=app.extensions['session_manager'].begin_upload(current_user.user_id,current_user.id,uuid_id(parts[4]),request.headers.get('X-Control-Token'))

    @app.teardown_request
    def release_frame_request(_):
        release=g.pop('preview_upload_release',None)
        if release:release()

    @app.after_request
    def envelope(response):
        if request.endpoint not in {'static','public_demo_asset'}:
            response.headers['Cache-Control']='no-store'
        # 文件型JSON是原始展示资源，保留流式响应及其摘要，不套API信封。
        if response.is_json and not response.direct_passthrough:
            result = response.get_json()
            if isinstance(result, dict) and "request_id" not in result:
                result["request_id"] = getattr(g,"request_id",str(uuid.uuid4()))
                response.set_data(app.json.dumps(result))
        return response

    @app.errorhandler(APIError)
    def api_error(error):
        if error.status in {403,404} and not request.path.startswith(('/api/','/static/','/_local/')):
            return render_template('unavailable.html',active='',title='内容暂不可用',message='内容不存在或当前账号没有访问权限，请返回首页。'),error.status
        return jsonify(error={"code":error.code,"message":error.message,"fields":error.fields,"retryable":False}),error.status

    @app.errorhandler(StorageError)
    @app.errorhandler(OperationalError)
    def storage_error(error):
        app.logger.error("存储不可用 request_id=%s type=%s",getattr(g,"request_id",""),type(error).__name__)
        return jsonify(error={"code":"STORAGE_UNAVAILABLE","message":"本机存储不可用，请核对状态后重试","fields":{},"retryable":True}),503

    @app.errorhandler(ConflictError)
    def conflict(error):
        return jsonify(error={"code":error.code,"message":str(error),"fields":{},"retryable":False}),409

    if db is not None:
        register_auth(app,db)
        from .pages import register_pages
        register_pages(app,db)
        from .admin import register_admin
        register_admin(app,db)
        from .record_routes import register_records
        register_records(app,db)
        from .preview_routes import register_previews
        register_previews(app)
        from .session_routes import register_sessions
        register_sessions(app,db)
        from .media_routes import register_media
        register_media(app,db)
        from .video_routes import register_video
        register_video(app,db)
        from .video_analysis_routes import register_video_analyses
        register_video_analyses(app)
        from .account import register_account
        register_account(app,db)
        from .training_info import register_training_info
        register_training_info(app)
        from .training_plans import register_training_plans
        register_training_plans(app)
        from .training_progress import register_progress
        register_progress(app)
        from .training_workouts import register_workouts
        register_workouts(app)
        from .training_allocations import register_allocations
        register_allocations(app)
        from .nutrition_routes import register_nutrition
        register_nutrition(app)
        from .nutrition_profiles import register_nutrition_profiles
        register_nutrition_profiles(app)
        from .training_insights import register_training_insights
        register_training_insights(app)
        from .retests import register_retests
        register_retests(app)
        from .coach_retests import register_coach_retests
        register_coach_retests(app)
        from .coach_practice import register_practice
        register_practice(app)
        from .skeleton_store import register_skeletons
        register_skeletons(app)
        from .stability_store import register_stability
        register_stability(app)
        from .report_exports import register_report_exports
        register_report_exports(app)
        from .ai_settings import register_ai_settings
        register_ai_settings(app)
        if app.config['CORRECTION_LABELS_ENABLED']:
            from .correction_labels import register_correction_labels
            register_correction_labels(app)
        if app.config['PRETRAINED_CORRECTION_ENABLED']:
            from .pretrained_routes import register_pretrained
            register_pretrained(app)
        from .ai_jobs import register_ai_jobs
        register_ai_jobs(app)
        from .coach_tasks import register_coach_tasks
        register_coach_tasks(app)
        from .training_jobs import register_training_jobs
        register_training_jobs(app)
        from .training_reviews import register_reviews
        register_reviews(app)
        from .training_overview import register_training_overview
        register_training_overview(app,db)

    @app.get("/api/v1/health")
    def health():
        resource = model_status(app.config.get("MODEL_PATH"))
        initialized = False
        if db is not None:
            with db.transaction(False) as connection:
                initialized = connection.scalar(select(app_meta.c.initialized_at_ms)) is not None
        return jsonify(data={"project_id": PROJECT_ID, "version": "0.1.0", "schema_version": SCHEMA_VERSION if db else None,
                             "initialized":initialized,
                             "instance_id": app.config.get("INSTANCE_ID"), "ready": resource["available"],
                             "model_resource": resource,
                             "versions": {name: importlib.metadata.version(name) for name in ("Flask", "mediapipe", "av")}},
                       request_id=str(uuid.uuid4()))

    @app.errorhandler(HTTPException)
    def http_error(error):
        if error.code in {403,404} and not request.path.startswith(('/api/','/static/','/_local/')):
            return render_template('unavailable.html',active='',title='页面不存在' if error.code==404 else '没有访问权限',message='请返回首页或从导航进入可用功能。'),error.code
        return jsonify(error={"code": "NOT_FOUND" if error.code == 404 else "INVALID_REQUEST",
                              "message": "资源不存在" if error.code == 404 else "请求无效",
                              "fields": {}, "retryable": False}, request_id=str(uuid.uuid4())), error.code

    @app.errorhandler(Exception)
    def unexpected_error(error):
        app.logger.error("请求处理失败：%s", type(error).__name__)
        return jsonify(error={"code": "INTERNAL_ERROR", "message": "处理失败，请核对请求状态", "fields": {}, "retryable": False},
                       request_id=str(uuid.uuid4())), 500

    return app
