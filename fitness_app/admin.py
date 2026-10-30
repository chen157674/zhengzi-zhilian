"""账号管理与最小操作回执；不授予查看他人训练记录的权限。"""
from __future__ import annotations

import json
import secrets
import uuid

from flask import jsonify, render_template, request
from flask_login import current_user
from sqlalchemy import select, update, func

from .auth import APIError, body, clock, normalized_username, password_hash, revoke_sessions, notify_revoked
from .database import idempotent, ConflictError
from .db_schema import users, auth_sessions, admin_operations, command_receipts


def uuid_value(value):
    try:
        return str(uuid.UUID(value))
    except (ValueError,TypeError,AttributeError) as error:
        raise APIError("INVALID_REQUEST","资源或操作标识格式不正确",400) from error


def command_body():
    command=uuid_value(request.headers.get("Idempotency-Key"))
    data=body({"expected_version","confirm"})
    if type(data.get("expected_version")) is not int or data["expected_version"]<0:
        raise APIError("VALIDATION_FAILED","账号版本不正确",422,{"expected_version":"请刷新账号状态"})
    if data.get("confirm") is not True:
        raise APIError("VALIDATION_FAILED","请先确认操作",422,{"confirm":"需要明确确认"})
    return command,data


def actor_in_transaction(connection):
    actor=connection.execute(select(users).where(users.c.id==current_user.user_id)).mappings().one()
    login=connection.execute(select(auth_sessions).where(auth_sessions.c.id==current_user.id)).mappings().first()
    if not actor["enabled"] or actor["role"]!="admin" or actor["auth_version"]!=current_user.auth_version:
        raise APIError("FORBIDDEN","管理员状态已变化，请重新登录核实",403)
    if not login or login["revoked_at_ms"] is not None or login["expires_at_ms"]<=clock():
        raise APIError("AUTH_REQUIRED","登录已失效",401)


def public_user(row,actor_id,admin_count):
    own=row["id"]==actor_id
    reset_reason="本人请使用修改密码" if own else ("停用账号不可重置" if not row["enabled"] else "")
    disable_reason="不能停用当前账号" if own else ("账号已停用" if not row["enabled"] else (
        "不能停用最后一个有效管理员" if row["role"]=="admin" and admin_count<=1 else ""))
    return {"id":row["id"],"username":row["username_display"],"role":row["role"],"enabled":bool(row["enabled"]),
        "must_change_password":bool(row["must_change_password"]),"version":row["version"],"created_at_ms":row["created_at_ms"],
        "can_reset":not bool(reset_reason),"reset_reason":reset_reason,
        "can_disable":not bool(disable_reason),"disable_reason":disable_reason}


def register_admin(app,db):
    @app.get("/admin/users")
    def users_page():
        return render_template("admin_users.html",active="")

    @app.get("/api/v1/admin/users")
    def user_list():
        if set(request.args)-{"search","enabled","page","page_size"}:
            raise APIError("INVALID_REQUEST","不支持的账号查询字段")
        search=request.args.get("search","")
        if len(search)>32:
            raise APIError("VALIDATION_FAILED","搜索内容过长",422,{"search":"最多 32 字符"})
        state=request.args.get("enabled","")
        if state not in {"","0","1"}:
            raise APIError("VALIDATION_FAILED","状态筛选无效",422)
        try:
            page=int(request.args.get("page","1")); size=int(request.args.get("page_size","20"))
        except ValueError as error:
            raise APIError("VALIDATION_FAILED","分页格式不正确",422) from error
        if page<1 or not 1<=size<=100:
            raise APIError("VALIDATION_FAILED","分页超出范围",422)
        conditions=[]
        if search.strip(): conditions.append(users.c.username_key.contains(normalized_username(search.strip()),autoescape=True))
        if state: conditions.append(users.c.enabled==int(state))
        with db.transaction(False) as c:
            total=c.scalar(select(func.count()).select_from(users).where(*conditions))
            page=min(page,max(1,(total+size-1)//size))
            rows=c.execute(select(users).where(*conditions).order_by(users.c.created_at_ms.desc(),users.c.id.desc())
                .offset((page-1)*size).limit(size)).mappings().all()
            admins=c.scalar(select(func.count()).select_from(users).where(users.c.role=="admin",users.c.enabled==1))
        return jsonify(data={"users":[public_user(row,current_user.user_id,admins) for row in rows],
                             "page":page,"page_size":size,"total":total})

    def mutate(user_id,operation):
        target_id=uuid_value(user_id)
        command,data=command_body()
        ephemeral={}
        with db.transaction() as c:
            actor_in_transaction(c)
            def execute():
                target=c.execute(select(users).where(users.c.id==target_id)).mappings().first()
                if not target: raise APIError("NOT_FOUND","账号不存在",404)
                if target["version"]!=data["expected_version"]: raise ConflictError("账号状态已更新，请刷新后重新确认")
                admin_count=c.scalar(select(func.count()).select_from(users).where(users.c.role=="admin",users.c.enabled==1))
                allowed=public_user(target,current_user.user_id,admin_count)
                reason=allowed["disable_reason"] if operation=="disable" else allowed["reset_reason"]
                if reason: raise APIError("INVALID_STATE",reason,409)
                now=clock()
                changes={"version":target["version"]+1,"auth_version":target["auth_version"]+1,"updated_at_ms":now}
                result={"id":target_id,"username":target["username_display"],"version":changes["version"],"operation_id":command}
                if operation=="disable":
                    changes["enabled"]=0
                    result["enabled"]=False
                else:
                    temporary=secrets.token_urlsafe(18)
                    expiry=now+30*60*1000
                    changes.update(temporary_password_hash=password_hash(temporary),temporary_expires_at_ms=expiry,must_change_password=1)
                    result["temporary_expires_at_ms"]=expiry
                    ephemeral["temporary_password"]=temporary
                c.execute(update(users).where(users.c.id==target_id).values(**changes))
                revoke_sessions(c,target_id,now)
                c.execute(admin_operations.insert().values(actor_user_id=current_user.user_id,target_user_id=target_id,
                    operation=operation,result="succeeded",command_id=command))
                ephemeral["executed"]=True
                return result
            result=idempotent(c,current_user.user_id,command,data,"account."+operation,execute,target_id)
        if ephemeral.get("executed"):
            notify_revoked(target_id)
        if operation=="reset":
            result=dict(result,credential_available="temporary_password" in ephemeral)
            if "temporary_password" in ephemeral:
                result["temporary_password"]=ephemeral.pop("temporary_password")
        return jsonify(data=result)

    @app.post("/api/v1/admin/users/<user_id>/disable")
    def disable(user_id):
        return mutate(user_id,"disable")

    @app.post("/api/v1/admin/users/<user_id>/password-reset")
    def reset(user_id):
        return mutate(user_id,"reset")

    @app.get("/api/v1/operations/<command_id>")
    def operation(command_id):
        command=uuid_value(command_id)
        with db.transaction(False) as c:
            result=c.execute(select(command_receipts).where(command_receipts.c.command_id==command,
                command_receipts.c.actor_user_id==current_user.user_id)).mappings().first()
        if not result: raise APIError("NOT_FOUND","操作不存在",404)
        if result['status']=='succeeded' and result['resource_type'] in {'records.delete','video.analysis.delete'}:
            callback=app.extensions.get('record_deletion_complete')
            if callback:callback(current_user.user_id,json.loads(result['result_json']).get('deleted_ids',[]))
        return jsonify(data={"operation_id":command,"status":result["status"],"resource_id":result["resource_id"],
                             "result":json.loads(result["result_json"]) if result["result_json"] else None})
