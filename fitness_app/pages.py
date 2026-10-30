"""B 款页面与认证路由；复用同一个 Flask 服务与后台能力目录。"""
from flask import render_template,request,redirect,jsonify,abort,current_app,send_file
from flask_login import current_user
from sqlalchemy import select
from .auth import safe_return_to
from .catalogue import definitions,get_action,seed_catalogue
from .db_schema import app_meta,user_profiles
from .profile_assets import avatar_url
from .presentation_assets import cover_catalogue,demo_resource,home_demo_catalogue,demo_display_track
from .paths import project_path


def register_pages(app,db):
    seed_catalogue(db)

    @app.context_processor
    def shared_page_context():
        avatar=None
        if current_user.is_authenticated:
            with db.transaction(False) as c:
                profile=c.execute(select(user_profiles).where(user_profiles.c.user_id==current_user.user_id)).mappings().first()
            avatar=avatar_url(profile['avatar_id'],profile['avatar_key'],profile['version']) if profile else avatar_url(None)
        return {"identity":current_user if current_user.is_authenticated else None,"header_avatar":avatar}

    @app.get('/account')
    def account_page():return render_template('account.html',active='')

    @app.get('/settings')
    def settings_page():return render_template('settings.html',active='')

    @app.get('/account/ai')
    def ai_settings_page():return render_template('ai_settings.html',active='')

    @app.get('/account/training-profile')
    def training_profile_page():return render_template('training_profile.html',active='')

    @app.get('/realtime')
    def automatic_page():return render_template('live.html',active='realtime',actions=definitions())

    @app.get('/account/sessions')
    def account_sessions_page():return render_template('account_sessions.html',active='')

    @app.before_request
    def page_access():
        if request.path.startswith(("/api/","/static/","/_local/")):
            return
        if current_user.is_authenticated and current_user.must_change_password and request.path!="/account/password":
            return redirect("/account/password?forced=1")
        public={"/","/login","/register"}
        if request.path not in public and not current_user.is_authenticated:
            from urllib.parse import quote
            return redirect("/login?returnTo="+quote(request.full_path.rstrip('?'),safe=''))
        if request.path.startswith("/admin/") and current_user.is_authenticated and current_user.role!="admin":
            return render_template("unavailable.html",active="",title="没有访问权限",message="当前账号不能访问管理工具。"),403

    @app.get("/")
    def home():
        covers=cover_catalogue()
        # 首页只展示已有配图的动作示例；完整动作目录在分析页，避免扩展后出现空白图片墙。
        demos=home_demo_catalogue()
        playlist=[{'id':item['id'],'title':item.get('display_title',item['title']),'video_url':item['video_url'],
            'track_url':item['track_url'],'poster_url':item['poster_url'],'source_sha':item['video']['sha256']} for item in demos]
        return render_template("home.html",active="home",demo=demos[0] if demos else None,demo_playlist=playlist,actions=[a for a in definitions() if a['action_id'] in covers],covers=covers)

    @app.get('/static/demo/<demo_id>/<asset>')
    def public_demo_asset(demo_id,asset):
        demo=demo_resource(demo_id,require_track=asset=='track')
        if not demo or asset not in {'video','track','poster'} or not demo.get(asset):abort(404)
        item=demo[asset]
        if asset=='track' and demo.get('display_annotation'):
            response=jsonify(demo_display_track(demo))
            response.set_etag(demo['track_etag'])
            response.cache_control.no_cache=True
            return response.make_conditional(request)
        return send_file(project_path(item['path']),mimetype={'video':'video/mp4','track':'application/json','poster':'image/jpeg'}[asset],conditional=True,etag=item['sha256'])

    @app.get("/login")
    @app.get("/register")
    @app.get("/account/password")
    def auth_page():
        kind={"/login":"login","/register":"register","/account/password":"password"}[request.path]
        if kind!="password" and current_user.is_authenticated:
            return redirect("/")
        with db.transaction(False) as c:
            initialized=c.scalar(select(app_meta.c.initialized_at_ms)) is not None
        name=request.args.get("username","")
        return render_template("auth.html",kind=kind,initialized=initialized,active="",username=name if len(name)<=32 else "",
            forced=bool(current_user.is_authenticated and current_user.must_change_password),
            return_to=safe_return_to(request.args.get("returnTo"),"/"),notice=request.args.get("notice",""))

    @app.get("/api/v1/actions")
    def actions_api():
        return jsonify(data={"actions":definitions()})

    @app.get("/api/v1/actions/<action_id>")
    def action_api(action_id):
        item=get_action(action_id)
        if item is None: abort(404)
        return jsonify(data=item)

    @app.get("/actions")
    def actions_page():
        return render_template("actions.html",active="realtime",actions=definitions(),covers=cover_catalogue(),source="video" if request.args.get("source")=="video" else "camera")

    @app.get("/prepare")
    def prepare_page():
        item=get_action(request.args.get("action",""))
        if not item: return redirect("/actions")
        return render_template("prepare.html",active="realtime",action=item,allow_observer=bool(current_app.config.get('TESTING') and current_app.config.get('ALLOW_POSE_ONLY_SESSIONS')))

