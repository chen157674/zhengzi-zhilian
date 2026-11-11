"""真实认证、受限临时登录和本机管理员维护（开发步骤 03）。"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import hmac
import secrets
import re
import unicodedata
import ipaddress
from urllib.parse import unquote, urlsplit

from flask import current_app, jsonify, request, session
from flask_login import LoginManager, UserMixin, current_user, login_user, logout_user
from flask_wtf.csrf import CSRFProtect, CSRFError, generate_csrf
from sqlalchemy import select, update, func
from sqlalchemy.exc import IntegrityError,OperationalError
from werkzeug.security import generate_password_hash, check_password_hash

from .database import Database, StorageError, ConflictError, maintenance_lock
from .db_schema import users, user_profiles, auth_sessions, app_meta, admin_operations, new_id, now_ms
from .profile_assets import random_profile,avatar_url
from .paths import runtime_path

USERNAME = re.compile(r"[A-Za-z0-9_\u3400-\u4dbf\u4e00-\u9fff]{2,32}\Z")
EMAIL_LOCAL = re.compile(r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*\Z")
COMMON_EMAIL_DOMAINS = frozenset({
    'qq.com', 'foxmail.com', '163.com', '126.com', 'yeah.net',
    'gmail.com', 'googlemail.com', 'outlook.com', 'hotmail.com', 'live.com', 'msn.com',
    'icloud.com', 'me.com', 'mac.com', 'yahoo.com', 'sina.com', 'sina.cn',
    'sohu.com', 'aliyun.com', '139.com', '189.cn', 'wo.cn', '21cn.com',
})
AUTH_TTL_MS = 12 * 60 * 60 * 1000


def local_setup_allowed():
    """首次账号仅允许从本机回环入口创建，不信任代理转发头。"""
    if current_app.config.get('MOBILE_PUBLIC_ORIGIN'):
        return False
    try:
        peer = ipaddress.ip_address(request.remote_addr or '')
        host = urlsplit(request.host_url).hostname
        return peer.is_loopback and host in {'127.0.0.1', 'localhost', '::1'}
    except ValueError:
        return False


class APIError(Exception):
    def __init__(self, code, message, status=400, fields=None):
        self.code, self.message, self.status = code, message, status
        self.fields = fields or {}


def normalized_username(value):
    return unicodedata.normalize("NFKC", value).casefold()


def validate_username(value):
    if not isinstance(value, str) or not USERNAME.fullmatch(value):
        raise APIError("VALIDATION_FAILED", "请检查用户名", 422,
                       {"username": "用户名为 2–32 位中英文、数字或下划线"})
    return normalized_username(value)


def validate_email(value, *, registration=False, field='email'):
    if not isinstance(value, str):
        raise APIError('VALIDATION_FAILED', '请检查登录账号', 422, {field: '请输入有效邮箱或2–32位用户名'})
    value = value.strip()
    local, separator, domain = value.rpartition('@')
    if (not separator or len(value) > 254 or not 1 <= len(local) <= 64
            or not EMAIL_LOCAL.fullmatch(local) or not re.fullmatch(r'[A-Za-z0-9]+(?:[.-][A-Za-z0-9]+)*\.[A-Za-z]{2,63}', domain)):
        raise APIError('VALIDATION_FAILED', '请检查邮箱', 422, {field: '请输入有效邮箱地址'})
    if registration and domain.lower() not in COMMON_EMAIL_DOMAINS:
        raise APIError('VALIDATION_FAILED', '暂不支持此邮箱', 422, {field: '请使用常见个人邮箱注册'})
    return value.lower()


def login_identifier(data):
    if ('email' in data) == ('username' in data):
        raise APIError('INVALID_REQUEST', '请提供一个登录账号')
    field = 'email' if 'email' in data else 'username'
    value = data[field]
    if isinstance(value, str) and '@' in value:
        return validate_email(value, field=field)
    # 仅登录兼容既有用户名；公共注册必须通过邮箱规则。
    if isinstance(value, str) and USERNAME.fullmatch(value):
        return normalized_username(value)
    raise APIError('VALIDATION_FAILED', '请检查邮箱', 422, {field: '请输入有效邮箱地址'})


def validate_password(value, field="password"):
    if not isinstance(value, str) or not 12 <= len(value) <= 128:
        raise APIError("VALIDATION_FAILED", "请检查密码", 422, {field: "密码为 12–128 字符，不自动删改空格"})
    return value


def password_hash(value):
    return generate_password_hash(value, method="scrypt")


def safe_return_to(value, default="/"):
    if not isinstance(value, str):
        return default
    if len(value)>2048:
        return default
    decoded = unquote(value)
    try:
        parts = urlsplit(decoded)
    except ValueError:
        return default
    if not decoded.startswith("/") or decoded.startswith("//") or "\\" in decoded or parts.scheme or parts.netloc or any(ord(c)<32 for c in decoded):
        return default
    return value


def body(fields,max_bytes=16384):
    if request.content_length and request.content_length > max_bytes:
        raise APIError("INPUT_TOO_LARGE", "表单数据超限", 413)
    data = request.get_json()
    if not isinstance(data, dict) or set(data) - set(fields):
        raise APIError("INVALID_REQUEST", "表单结构或字段不合法")
    return data


def clock():
    return current_app.config.get("CLOCK", now_ms)()


class Identity(UserMixin):
    def __init__(self, user, auth):
        self.id = auth["id"]
        self.user_id = user["id"]
        self.username = user["username_display"]
        self.nickname = user.get('nickname') or self.username
        self.avatar_id = user.get('avatar_id') or 'avatar-01'
        self.avatar_url = avatar_url(self.avatar_id,user.get('avatar_key'),user.get('profile_version') or 0)
        self.avatar_source = 'uploaded' if user.get('avatar_key') else 'builtin'
        self.profile_version = user.get('profile_version') or 0
        self.role = user["role"]
        self.must_change_password = bool(user["must_change_password"])
        self.auth_version = user["auth_version"]
        self.version = user["version"]
        self.expires_at_ms = auth["expires_at_ms"]
        self.temporary_login = bool(auth["temporary_login"])

    def public(self):
        return {"id": self.user_id, "username": self.username, "email": self.username if "@" in self.username else None, "role": self.role,
                "must_change_password": self.must_change_password, "expires_at_ms": self.expires_at_ms,
                "version": self.version,"nickname":self.nickname,'avatar_id':self.avatar_id,
                'avatar_url':self.avatar_url,'avatar_source':self.avatar_source,'profile_version':self.profile_version}


def notify_revoked(user_id, auth_session_id=None):
    for callback in current_app.extensions.get("auth_revoke_hooks", []):
        callback(user_id=user_id, auth_session_id=auth_session_id)


def revoke_sessions(connection, user_id, timestamp, auth_session_id=None):
    condition = [auth_sessions.c.user_id == user_id, auth_sessions.c.revoked_at_ms.is_(None)]
    if auth_session_id:
        condition.append(auth_sessions.c.id == auth_session_id)
    connection.execute(update(auth_sessions).where(*condition).values(revoked_at_ms=timestamp))


def register_auth(app, db: Database):
    login_manager = LoginManager(app)
    app.config["WTF_CSRF_CHECK_DEFAULT"] = False
    csrf = CSRFProtect(app)
    dummy_hash = password_hash(secrets.token_urlsafe(24))
    app.extensions["auth_revoke_hooks"] = []

    @login_manager.user_loader
    def load_identity(auth_id):
        raw_token = session.get("auth_token")
        if not isinstance(raw_token, str):
            return None
        with db.transaction(False) as c:
            auth = c.execute(select(auth_sessions).where(auth_sessions.c.id == auth_id)).mappings().first()
            if not auth:
                return None
            user = c.execute(select(users,user_profiles.c.nickname,user_profiles.c.avatar_id,user_profiles.c.avatar_key,user_profiles.c.version.label('profile_version'))
                             .outerjoin(user_profiles,users.c.id==user_profiles.c.user_id).where(users.c.id == auth["user_id"])).mappings().first()
        if not user or not user["enabled"] or auth["revoked_at_ms"] is not None or auth["expires_at_ms"] <= clock():
            return None
        if user["auth_version"] != auth["auth_version"] or not hmac.compare_digest(auth["token_hash"], hashlib.sha256(raw_token.encode()).hexdigest()):
            return None
        if auth["temporary_login"] and (not user["must_change_password"] or not user["temporary_expires_at_ms"] or user["temporary_expires_at_ms"] <= clock()):
            return None
        return Identity(user, auth)

    public = {"/api/v1/auth/setup", "/api/v1/health", "/api/v1/auth/csrf", "/api/v1/auth/register", "/api/v1/auth/login", "/api/v1/auth/logout"}
    forced_allowed = {"/api/v1/auth/me", "/api/v1/auth/password", "/api/v1/auth/csrf", "/api/v1/auth/logout", "/api/v1/health"}

    @app.before_request
    def protect_requests():
        if request.path.startswith("/api/v1/"):
            try:authenticated=current_user.is_authenticated
            except (StorageError,OperationalError,OSError):
                from flask import g
                manager=app.extensions.get('session_manager');parts=request.path.split('/')
                if not manager or request.method!='POST' or len(parts)!=6 or parts[1:4]!=['api','v1','sessions'] or parts[-1]!='commands':raise
                payload=request.get_json(silent=True)
                g.cleanup_identity=manager.cleanup_identity(session.get('_user_id'),parts[4],payload)
                authenticated=True
            from flask import g
            cleanup=getattr(g,'cleanup_identity',None)
            if request.path not in public and not authenticated:
                raise APIError("AUTH_REQUIRED", "请先登录", 401)
            if not cleanup and authenticated and current_user.must_change_password and request.path not in forced_allowed:
                raise APIError("PASSWORD_CHANGE_REQUIRED", "请先修改临时密码", 403)
            if not cleanup and request.path.startswith("/api/v1/admin/") and authenticated and current_user.role != "admin":
                raise APIError("FORBIDDEN", "需要管理员权限", 403)
        if request.method not in {"GET", "HEAD", "OPTIONS"} and request.path != "/_local/stop":
            csrf.protect()

    @app.errorhandler(CSRFError)
    def csrf_failure(_):
        raise_error = APIError("CSRF_INVALID", "表单凭据已失效，请重新读取后再操作", 403)
        return app.handle_user_exception(raise_error)

    @app.get("/api/v1/auth/csrf")
    def csrf_token():
        return jsonify(data={"csrf_token": generate_csrf()})

    @app.post("/api/v1/auth/register")
    def register():
        data = body({"email", "username", "password", "password_confirmation"})
        if ('email' in data) == ('username' in data):
            raise APIError('INVALID_REQUEST', '请提供一个注册邮箱')
        field = 'email' if 'email' in data else 'username'
        key = validate_email(data.get(field), registration=True, field=field)
        validate_password(data.get("password"))
        if data["password"] != data.get("password_confirmation"):
            raise APIError("VALIDATION_FAILED", "两次密码不一致", 422, {"password_confirmation": "请再次输入相同密码"})
        hashed = password_hash(data["password"])
        uid=new_id();profile=random_profile()
        try:
            with db.transaction() as c:
                if c.scalar(select(app_meta.c.initialized_at_ms)) is None:
                    raise APIError("INITIALIZATION_REQUIRED", "请先通过本机入口初始化管理员", 409)
                c.execute(users.insert().values(id=uid,username_display=key, username_key=key, password_hash=hashed))
                c.execute(user_profiles.insert().values(user_id=uid,**profile))
        except IntegrityError as error:
            if "users.username_key" not in str(error.orig):
                raise
            raise APIError("EMAIL_TAKEN", "该邮箱已注册", 409, {field: "该邮箱已注册，请直接登录"}) from error
        return jsonify(data={"username": key, "email": key, "registered": True,**profile,'avatar_url':avatar_url(profile['avatar_id'])}), 201

    @app.post('/api/v1/auth/setup')
    def setup():
        if not local_setup_allowed():
            raise APIError('LOCAL_SETUP_ONLY', '首次设置请在运行此应用的电脑上完成', 403)
        data = body({'email', 'password', 'password_confirmation'})
        name = data.get('email')
        key = validate_email(name) if isinstance(name, str) and '@' in name else validate_username(name)
        validate_password(data.get('password'))
        if data['password'] != data.get('password_confirmation'):
            raise APIError('VALIDATION_FAILED', '两次密码不一致', 422,
                           {'password_confirmation': '请再次输入相同密码'})
        hashed = password_hash(data['password'])
        uid = new_id()
        # BEGIN IMMEDIATE串行化首次设置；已有账号绝不覆盖或提权。
        with db.transaction() as c:
            if c.scalar(select(app_meta.c.initialized_at_ms)) is not None or c.scalar(select(func.count()).select_from(users)):
                raise APIError('ALREADY_INITIALIZED', '账号已经设置，请直接登录', 409)
            c.execute(users.insert().values(id=uid, username_display=name,
                       username_key=key, password_hash=hashed, role='admin'))
            c.execute(user_profiles.insert().values(user_id=uid, **random_profile()))
            c.execute(update(app_meta).where(app_meta.c.id == 1).values(initialized_at_ms=clock()))
            c.execute(admin_operations.insert().values(actor_user_id=uid, target_user_id=uid,
                       operation='initialize', result='succeeded', command_id=new_id(), source='local_web_setup'))
        return jsonify(data={'initialized': True, 'username': name}), 201

    @app.post("/api/v1/auth/login")
    def login():
        data = body({"email", "username", "password"})
        name, password = login_identifier(data), data.get("password")
        if not isinstance(password, str) or len(password)>128:
            raise APIError("VALIDATION_FAILED", "请填写有效账号和密码", 422)
        with db.transaction(False) as c:
            user = c.execute(select(users).where(users.c.username_key == normalized_username(name))).mappings().first()
        temporary = bool(user and user["must_change_password"])
        candidate_hash = (user["temporary_password_hash"] if temporary else user["password_hash"]) if user else dummy_hash
        valid = check_password_hash(candidate_hash or dummy_hash, password)
        if not user or not user["enabled"] or not valid or (temporary and (not user["temporary_expires_at_ms"] or user["temporary_expires_at_ms"] <= clock())):
            raise APIError("AUTH_FAILED", "邮箱、用户名或密码错误，或账号暂不可用", 401)
        raw = secrets.token_urlsafe(32)
        auth_id = new_id()
        now = clock()
        expiry = min(now + AUTH_TTL_MS, user["temporary_expires_at_ms"]) if temporary else now + AUTH_TTL_MS
        with db.transaction() as c:
            fresh = c.execute(select(users).where(users.c.id == user["id"])).mappings().one()
            if not fresh["enabled"] or fresh["auth_version"] != user["auth_version"]:
                raise APIError("AUTH_FAILED", "账号状态已变化，请重新登录", 401)
            c.execute(auth_sessions.insert().values(id=auth_id, user_id=user["id"], token_hash=hashlib.sha256(raw.encode()).hexdigest(),
                auth_version=user["auth_version"], created_at_ms=now, expires_at_ms=expiry, temporary_login=int(temporary)))
        session.clear()
        session.permanent = True
        session["auth_token"] = raw
        identity = Identity(user, {"id": auth_id, "expires_at_ms": expiry, "temporary_login": temporary})
        login_user(identity, remember=False, fresh=True)
        return jsonify(data=identity.public())

    @app.get("/api/v1/auth/me")
    def me():
        return jsonify(data=current_user.public())

    @app.post("/api/v1/auth/logout")
    def logout():
        if current_user.is_authenticated:
            uid, sid = current_user.user_id, current_user.id
            with db.transaction() as c:
                revoke_sessions(c, uid, clock(), sid)
            notify_revoked(uid, sid)
        logout_user()
        session.clear()
        return jsonify(data={"logged_out": True})

    @app.post("/api/v1/auth/password")
    def change_password():
        data = body({"current_password", "new_password", "password_confirmation"})
        validate_password(data.get("new_password"), "new_password")
        if data["new_password"] != data.get("password_confirmation"):
            raise APIError("VALIDATION_FAILED", "两次新密码不一致", 422, {"password_confirmation": "请再次输入相同密码"})
        uid = current_user.user_id
        with db.transaction(False) as c:
            user = c.execute(select(users).where(users.c.id == uid)).mappings().one()
        old_hash = user["temporary_password_hash"] if current_user.temporary_login else user["password_hash"]
        if not isinstance(data.get("current_password"), str) or len(data["current_password"])>128 or not check_password_hash(old_hash or dummy_hash, data["current_password"]):
            raise APIError("VALIDATION_FAILED", "当前密码不正确", 422, {"current_password": "请核对当前密码"})
        hashed = password_hash(data["new_password"])
        now = clock()
        with db.transaction() as c:
            result = c.execute(update(users).where(users.c.id==uid, users.c.auth_version==user["auth_version"], users.c.enabled==1).values(
                password_hash=hashed, temporary_password_hash=None, temporary_expires_at_ms=None,
                must_change_password=0, auth_version=user["auth_version"]+1, version=user["version"]+1, updated_at_ms=now))
            if result.rowcount != 1:
                raise ConflictError("账号状态已改变，请重新登录核实")
            revoke_sessions(c, uid, now)
        notify_revoked(uid)
        logout_user()
        session.clear()
        return jsonify(data={"changed": True, "reauthentication_required": True})


@contextmanager
def admin_transaction(db):
    with maintenance_lock(runtime_path()/"service.lock", exact=True):
        db.initialize()
        with maintenance_lock(db.path), db.transaction() as c:
            yield c


def initialize_admin(db, username, password):
    key = validate_email(username) if isinstance(username, str) and '@' in username else validate_username(username)
    hashed = password_hash(validate_password(password))
    uid = new_id()
    timestamp = now_ms()
    with admin_transaction(db) as c:
        if c.scalar(select(app_meta.c.initialized_at_ms)) is not None or c.scalar(select(func.count()).select_from(users)):
            raise ConflictError("已有账号或初始化标识，不能再次初始化")
        c.execute(users.insert().values(id=uid, username_display=username, username_key=key, password_hash=hashed, role="admin"))
        c.execute(user_profiles.insert().values(user_id=uid,**random_profile()))
        c.execute(update(app_meta).where(app_meta.c.id==1).values(initialized_at_ms=timestamp))
        c.execute(admin_operations.insert().values(actor_user_id=uid,target_user_id=uid,operation="initialize",result="succeeded",command_id=new_id(),source="local_init"))
    return {"id": uid, "username": username}


def recover_admin(db, username, password):
    key = validate_email(username) if isinstance(username, str) and '@' in username else validate_username(username)
    hashed = password_hash(validate_password(password))
    now = now_ms()
    with admin_transaction(db) as c:
        user = c.execute(select(users).where(users.c.username_key==key,users.c.role=="admin",users.c.enabled==1)).mappings().first()
        if not user:
            raise ConflictError("仅允许恢复既有有效管理员，不创建／提权／解停账号")
        c.execute(update(users).where(users.c.id==user["id"]).values(password_hash=hashed,temporary_password_hash=None,
            temporary_expires_at_ms=None,must_change_password=0,auth_version=user["auth_version"]+1,version=user["version"]+1,updated_at_ms=now))
        revoke_sessions(c,user["id"],now)
        c.execute(admin_operations.insert().values(actor_user_id=user["id"],target_user_id=user["id"],operation="recover",result="succeeded",command_id=new_id(),source="local_recovery"))
    return {"id":user["id"],"username":user["username_display"]}
