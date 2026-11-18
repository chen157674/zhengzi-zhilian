"""个人中心后端：本人资料、记录摘要与登录管理，不承载他人训练资料。"""
import unicodedata
import os
import uuid
from flask import jsonify,request,send_file
from flask_login import current_user
from sqlalchemy import select,update,func,case,and_
from werkzeug.security import check_password_hash
from . import db_schema as s
from .auth import APIError,body,clock,notify_revoked
from .database import ConflictError,StorageError
from .profile_assets import avatar_url,random_profile
from .avatar_upload import decode_avatar,stored_avatar
from .paths import project_path,runtime_path
from .records import integer,TERMINAL,effective_records


def register_account(app,db):
    avatar_root=project_path(db.path.parent/'user-avatars')
    app.config['AVATAR_STORAGE_ROOT']=str(avatar_root)
    app.config['AVATAR_SPOOL_ROOT']=str(project_path(db.path.parent/'avatar-spool' if app.config.get('TESTING') else runtime_path()/'avatar-inputs'))
    def identity(c):
        user=c.execute(select(s.users).where(s.users.c.id==current_user.user_id)).mappings().first()
        auth=c.execute(select(s.auth_sessions).where(s.auth_sessions.c.id==current_user.id)).mappings().first()
        if not user or not user['enabled'] or not auth or auth['user_id']!=user['id'] or auth['revoked_at_ms'] is not None or auth['expires_at_ms']<=clock() or auth['auth_version']!=user['auth_version']:
            raise APIError('AUTH_REQUIRED','登录状态已变化，请重新登录',401)
        return user

    def profile(c,user):
        row=c.execute(select(s.user_profiles).where(s.user_profiles.c.user_id==user['id'])).mappings().first()
        if not row:
            c.execute(s.user_profiles.insert().values(user_id=user['id'],**random_profile()))
            row=c.execute(select(s.user_profiles).where(s.user_profiles.c.user_id==user['id'])).mappings().one()
        return {'user_id':user['id'],'username':user['username_display'],'nickname':row['nickname'],'avatar_id':row['avatar_id'],
                'avatar_url':avatar_url(row['avatar_id'],row['avatar_key'],row['version']),'avatar_source':'uploaded' if row['avatar_key'] else 'builtin',
                'avatar_upload_url':'/api/v1/account/avatar','profile_version':row['version'],'created_at_ms':user['created_at_ms'],
                'role':user['role'],'records_url':'/api/v1/records','password_url':'/api/v1/auth/password'}

    @app.get('/api/v1/account/profile')
    def get_profile():
        with db.transaction() as c:result=profile(c,identity(c))
        return jsonify(data=result)

    @app.post('/api/v1/account/profile')
    def update_profile():
        data=body({'nickname','expected_version'})
        integer(data.get('expected_version'),'expected_version')
        changes={}
        if 'nickname' in data:
            name=data['nickname']
            if not isinstance(name,str):raise APIError('VALIDATION_FAILED','昵称格式无效',422)
            name=unicodedata.normalize('NFC',name).strip()
            if not 2<=len(name)<=24 or any(unicodedata.category(ch).startswith('C') or ch in '<>&' for ch in name):
                raise APIError('VALIDATION_FAILED','请输入2～24个可见字符作为昵称',422,{'nickname':'请检查昵称'})
            changes['nickname']=name
        if not changes:raise APIError('VALIDATION_FAILED','请输入要修改的昵称',422)
        with db.transaction() as c:
            user=identity(c);profile(c,user)
            changed=c.execute(update(s.user_profiles).where(s.user_profiles.c.user_id==user['id'],s.user_profiles.c.version==data['expected_version'])
                .values(**changes,version=s.user_profiles.c.version+1,updated_at_ms=clock()))
            if changed.rowcount!=1:raise ConflictError('个人资料已更新，请重新读取后修改')
            result=profile(c,user)
        return jsonify(data=result)

    @app.get('/api/v1/account/avatar')
    def get_avatar():
        with db.transaction(False) as c:
            user=identity(c)
            key=c.scalar(select(s.user_profiles.c.avatar_key).where(s.user_profiles.c.user_id==user['id']))
        if not key:raise APIError('NOT_FOUND','请上传自定义头像',404)
        path=stored_avatar(avatar_root,key)
        if not path.is_file():raise APIError('NOT_FOUND','头像文件暂不可用，请重新上传',404)
        try:return send_file(path,mimetype='image/jpeg',conditional=False,max_age=0)
        except FileNotFoundError as error:raise APIError('NOT_FOUND','头像已更新，请重新读取个人资料',404) from error

    @app.post('/api/v1/account/avatar')
    def upload_avatar():
        if request.mimetype!='multipart/form-data' or set(request.form)!={'expected_version'} or len(request.form.getlist('expected_version'))!=1 or set(request.files)!={'file'} or len(request.files.getlist('file'))!=1:
            raise APIError('INVALID_REQUEST','需要一张file图片和资料版本expected_version',400)
        raw_version=request.form['expected_version']
        if not raw_version.isascii() or not raw_version.isdigit() or len(raw_version)>10:raise APIError('VALIDATION_FAILED','资料版本无效',422)
        version=integer(int(raw_version),'expected_version')
        encoded=decode_avatar(request.files['file'].stream);new_key=None;old_key=None;path=None;owns_new_file=False
        try:
            with db.transaction() as c:
                user=identity(c);current=profile(c,user)
                if current['profile_version']!=version:raise ConflictError('个人资料已更新，请重新读取后上传')
                old_key=c.scalar(select(s.user_profiles.c.avatar_key).where(s.user_profiles.c.user_id==user['id']))
                new_key=uuid.uuid4().hex+'.jpg';path=stored_avatar(avatar_root,new_key)
                avatar_root.mkdir(parents=True,exist_ok=True)
                with path.open('xb') as output:
                    owns_new_file=True
                    output.write(encoded);output.flush();os.fsync(output.fileno())
                c.execute(update(s.user_profiles).where(s.user_profiles.c.user_id==user['id'],s.user_profiles.c.version==version)
                          .values(avatar_key=new_key,version=s.user_profiles.c.version+1,updated_at_ms=clock()))
                result=profile(c,user)
        except Exception as operation_error:
            if owns_new_file and new_key and path and path.exists():
                # 提交回执不确定时保留可能已被资料引用的文件，不制造悬空头像。
                try:
                    with db.transaction(False) as c:current_key=c.scalar(select(s.user_profiles.c.avatar_key).where(s.user_profiles.c.user_id==current_user.user_id))
                    if current_key!=new_key:path.unlink(missing_ok=True)
                except Exception as cleanup_error:app.logger.warning('头像文件状态待复核 type=%s',type(cleanup_error).__name__)
            if isinstance(operation_error,OSError):raise StorageError('头像存储不可用，原头像保留') from operation_error
            raise
        if old_key and old_key!=new_key:
            try:stored_avatar(avatar_root,old_key).unlink(missing_ok=True)
            except OSError as error:app.logger.warning('旧头像副本待清理 type=%s',type(error).__name__)
        return jsonify(data=result)

    @app.get('/api/v1/account/overview')
    def overview():
        with db.transaction(False) as c:
            user=identity(c)
            condition=(s.analysis_sessions.c.user_id==user['id'],s.analysis_sessions.c.source!='demo',
                       s.analysis_sessions.c.started_at_ms.is_not(None),s.analysis_sessions.c.status.in_(TERMINAL),effective_records())
            stats=c.execute(select(func.count().label('total_sessions'),func.coalesce(func.sum(s.analysis_sessions.c.active_ms),0).label('total_active_ms'),
                                   func.max(s.analysis_sessions.c.started_at_ms).label('last_activity_ms')).where(*condition)).mappings().one()
            # 读取记录自己的能力快照；目录后续开放计数不能回写历史可用性。
            definition=s.action_capabilities.c.definition_json
            available=func.coalesce(func.json_extract(definition,'$.counting_available'),0)==1
            unit=func.json_extract(definition,'$.unit')
            reps=and_(available,unit.in_(('reps','per_side_reps')));hold=and_(available,unit=='seconds')
            amounts=c.execute(select(
                func.coalesce(func.sum(case((reps,s.session_sets.c.total_count),else_=0)),0).label('reference_reps'),
                func.coalesce(func.sum(case((hold,s.session_sets.c.hold_ms),else_=0)),0).label('reference_hold_ms'),
                func.count(case((reps,s.analysis_sessions.c.id)).distinct()).label('rep_sessions'),
                func.count(case((hold,s.analysis_sessions.c.id)).distinct()).label('hold_sessions'))
                .select_from(s.session_sets.join(s.analysis_sessions,s.session_sets.c.session_id==s.analysis_sessions.c.id)
                    .join(s.action_capabilities,and_(s.action_capabilities.c.action_id==s.analysis_sessions.c.action_id,
                                                   s.action_capabilities.c.capability_version==s.analysis_sessions.c.capability_version)))
                .where(*condition)).mappings().one()
        completion={name:{'status':'available' if count else 'not_available','value':value if count else None,
                          'unit':unit,'session_count':count}
                    for name,value,unit,count in (('reps',amounts['reference_reps'],'reps',amounts['rep_sessions']),
                                                  ('hold',amounts['reference_hold_ms'],'ms',amounts['hold_sessions']))}
        completion.update(schema_version=1,unavailable_sessions=stats['total_sessions']-amounts['rep_sessions']-amounts['hold_sessions'],
                          scope='完成量统计范围：已保存的动作计数记录；空值表示待补充计数记录。')
        recent=app.extensions['session_store'].list_records(current_user.user_id,1,5)
        return jsonify(data={**dict(stats),'reference_reps':amounts['reference_reps'],'reference_hold_ms':amounts['reference_hold_ms'],
                             'completion_summary':completion,'recent_records':recent['records'],
                             'summary_scope':'已开始并结束的本人摄像头／视频记录；排除演示；完成量为参考统计'})

    @app.get('/api/v1/account/sessions')
    def sessions():
        with db.transaction(False) as c:
            user=identity(c)
            rows=c.execute(select(s.auth_sessions.c.id,s.auth_sessions.c.created_at_ms,s.auth_sessions.c.expires_at_ms)
                           .where(s.auth_sessions.c.user_id==user['id'],s.auth_sessions.c.revoked_at_ms.is_(None),
                                  s.auth_sessions.c.auth_version==user['auth_version'],s.auth_sessions.c.expires_at_ms>clock())
                           .order_by(s.auth_sessions.c.created_at_ms.desc()).limit(100)).mappings().all()
        return jsonify(data={'sessions':[{'session_id':r['id'],'created_at_ms':r['created_at_ms'],'expires_at_ms':r['expires_at_ms'],
                                         'current':r['id']==current_user.id} for r in rows]})

    @app.post('/api/v1/account/sessions/revoke-others')
    def revoke_others():
        data=body({'current_password'});password=data.get('current_password')
        with db.transaction() as c:
            user=identity(c)
            if not isinstance(password,str) or len(password)>128 or not check_password_hash(user['password_hash'],password):
                raise APIError('VALIDATION_FAILED','当前密码不正确',422,{'current_password':'请核对当前密码'})
            ids=c.execute(select(s.auth_sessions.c.id).where(s.auth_sessions.c.user_id==user['id'],s.auth_sessions.c.id!=current_user.id,
                          s.auth_sessions.c.revoked_at_ms.is_(None),s.auth_sessions.c.expires_at_ms>clock())).scalars().all()
            if ids:c.execute(update(s.auth_sessions).where(s.auth_sessions.c.id.in_(ids)).values(revoked_at_ms=clock()))
        for auth_id in ids:notify_revoked(user['id'],auth_id)
        return jsonify(data={'revoked_count':len(ids),'current_session_kept':True})
