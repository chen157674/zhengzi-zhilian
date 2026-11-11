"""本人训练档案及自述负重/难度；不改检测事件、不自动推断负重。"""
from decimal import Decimal,InvalidOperation
import json
import unicodedata
from flask import jsonify,request
from flask_login import current_user
from sqlalchemy import select,update
from . import db_schema as s
from .auth import APIError,body,clock
from .database import ConflictError,idempotent,json_text
from .records import integer,uuid_id

EXPERIENCE={'beginner','intermediate','experienced'}
GOALS={'movement_quality','training_habit','strength','general_fitness','muscle_gain','fat_loss'}
EQUIPMENT={'bodyweight','barbell','dumbbell','machine','cable','bands','other'}
LOAD_UNITS={'kg','lb','plate_index'}
LOAD_BASES={'per_hand','per_side','total','device_setting','unspecified'}
DIFFICULTIES={'easy','moderate','hard'}
PROFILE_FIELDS={'experience','goals','equipment','available_minutes','height_cm','weight_kg','age_years'}
NOTE_FIELDS={'load_value','load_unit','load_basis','difficulty','note'}


def invalid(field,message='请检查填写内容'):
    raise APIError('VALIDATION_FAILED',message,422,{field:message})


def choice(value,allowed,field):
    if value is not None and (not isinstance(value,str) or value not in allowed):invalid(field)
    return value


def choices(value,allowed,field):
    if value is None:return []
    if not isinstance(value,list) or len(value)>len(allowed):invalid(field)
    if any(not isinstance(v,str) or v not in allowed for v in value):invalid(field)
    if len(value)!=len(set(value)):invalid(field,'选项不能重复')
    return list(value)


def profile_values(data,previous):
    values=dict(previous)
    for key in PROFILE_FIELDS&data.keys():
        value=data[key]
        if key=='experience':values[key]=choice(value,EXPERIENCE,key)
        elif key in {'goals','equipment'}:values[key]=choices(value,GOALS if key=='goals' else EQUIPMENT,key)
        elif key in {'height_cm','weight_kg'}:
            if value is None:values[key]=None;continue
            if type(value) not in (int,float):invalid(key,'请填写有效数字')
            number=Decimal(str(value));maximum=300 if key=='height_cm' else 500
            if not number.is_finite() or not 0<number<=maximum:invalid(key,'请检查身高或体重数值')
            values[key]=float(number)
        elif key=='age_years':values[key]=None if value is None else integer(value,key,1,120)
        else:values[key]=None if value is None else integer(value,key,1,600)
    return values


def note_values(data):
    values={key:data.get(key) for key in NOTE_FIELDS}
    values['load_unit']=choice(values['load_unit'],LOAD_UNITS,'load_unit')
    values['load_basis']=choice(values['load_basis'],LOAD_BASES,'load_basis')
    values['difficulty']=choice(values['difficulty'],DIFFICULTIES,'difficulty')
    value=values['load_value']
    if value is not None:
        if type(value) not in (int,float):invalid('load_value')
        try:number=Decimal(str(value))
        except InvalidOperation:invalid('load_value')
        if not number.is_finite() or number<0 or number>1000000 or number.as_tuple().exponent < -2:invalid('load_value','请输入非负数，最多两位小数')
        if not values['load_unit'] or not values['load_basis']:invalid('load_unit','填写负重时需要单位和重量口径')
        if values['load_unit']=='plate_index':
            if number<1 or number!=number.to_integral_value() or values['load_basis']!='device_setting':invalid('load_value','配重档位需为正整数，口径为器械档位')
            values['load_value']=int(number)
        else:
            if values['load_basis']=='device_setting':invalid('load_basis','器械档位不能当作公斤或磅')
            values['load_value']=float(number)
    elif values['load_unit'] is not None or values['load_basis'] is not None:
        invalid('load_value','未填写负重时请同时清空单位与口径')
    note=values['note']
    if note is not None:
        if not isinstance(note,str):invalid('note')
        note=unicodedata.normalize('NFC',note).strip()
        if len(note)>500 or any(unicodedata.category(ch).startswith('C') and ch not in '\n\t' for ch in note):invalid('note','备注最多500字，不含控制字符')
        values['note']=note or None
    return values


class TrainingInfoStore:
    def __init__(self,sessions,clock=s.now_ms):
        self.sessions=sessions;self.db=sessions.db;self.clock=clock

    def profile(self,c,uid):
        row=c.execute(select(s.training_profiles).where(s.training_profiles.c.user_id==uid)).mappings().first()
        values=json.loads(row['data_json']) if row else {}
        return {'version':row['version'] if row else 0,'updated_at_ms':row['updated_at_ms'] if row else None,
                'experience':values.get('experience'),'goals':values.get('goals',[]),'equipment':values.get('equipment',[]),
                'available_minutes':values.get('available_minutes'),**{k:values.get(k) for k in ('height_cm','weight_kg','age_years')},'source':'user_provided','optional':True}

    def note(self,c,uid,sid):
        self.sessions._session(c,uid,sid)
        row=c.execute(select(s.training_notes).where(s.training_notes.c.session_id==sid,s.training_notes.c.user_id==uid)).mappings().first()
        values=json.loads(row['data_json']) if row else {}
        return {'session_id':sid,'version':row['version'] if row else 0,'updated_at_ms':row['updated_at_ms'] if row else None,
                **{k:values.get(k) for k in NOTE_FIELDS},'source':'user_provided','difficulty_is_subjective':True,'inferred_from_video':False}

    def get_profile(self,uid,auth):
        with self.db.transaction(False) as c:
            self.sessions._actor(c,uid,auth)
            return self.profile(c,uid)

    def get_note(self,uid,auth,sid):
        with self.db.transaction(False) as c:
            self.sessions._actor(c,uid,auth)
            return self.note(c,uid,sid)

    def write(self,uid,auth,payload,key,sid=None,clear=False):
        key=uuid_id(key);version=integer(payload.get('expected_version'),'expected_version')
        with self.db.transaction() as c:
            self.sessions._actor(c,uid,auth)
            if sid:self.sessions._session(c,uid,sid)
            # 先完成字段校验，再计算不允许NaN的幂等摘要，非法输入应返回422而非500。
            prepared=None
            if not clear:
                if sid:prepared=note_values(payload)
                else:profile_values(payload,{})
            table=s.training_notes if sid else s.training_profiles
            column=table.c.session_id if sid else table.c.user_id
            identity=sid or uid
            def change():
                row=c.execute(select(table).where(column==identity)).mappings().first()
                current=row['version'] if row else 0
                if current!=version:raise ConflictError('内容已更新，请重新读取后修改')
                previous=json.loads(row['data_json']) if row else {}
                values={} if clear else prepared if sid else profile_values(payload,previous)
                saved={'data_json':json_text(values),'version':current+1,'updated_at_ms':self.clock()}
                if row:c.execute(update(table).where(column==identity).values(**saved))
                else:c.execute(table.insert().values(**saved,**({'session_id':sid,'user_id':uid} if sid else {'user_id':uid})))
                return {'id':identity,'version':current+1}
            idempotent(c,uid,key,{**payload,'clear':clear},'training.note' if sid else 'training.profile',change,identity)
            # 回执只保存身份/版本，清除后不从旧回执重新展示或送给AI已清除资料。
            return self.note(c,uid,sid) if sid else self.profile(c,uid)


def register_training_info(app):
    store=TrainingInfoStore(app.extensions['session_store'],app.config.get('CLOCK') or s.now_ms)
    app.extensions['training_info']=store

    @app.get('/api/v1/me/training-profile')
    def training_profile_get():return jsonify(data=store.get_profile(current_user.user_id,current_user.id))

    @app.route('/api/v1/me/training-profile',methods=['PATCH','DELETE'])
    def training_profile_write():
        clear=request.method=='DELETE';data=body({'expected_version'} if clear else PROFILE_FIELDS|{'expected_version'})
        if not clear and not PROFILE_FIELDS&data.keys():invalid('profile','请填写或清除至少一项资料')
        return jsonify(data=store.write(current_user.user_id,current_user.id,data,request.headers.get('Idempotency-Key'),clear=clear))

    @app.get('/api/v1/records/<session_id>/training-note')
    def training_note_get(session_id):return jsonify(data=store.get_note(current_user.user_id,current_user.id,uuid_id(session_id)))

    @app.put('/api/v1/records/<session_id>/training-note')
    def training_note_write(session_id):
        data=body(NOTE_FIELDS|{'expected_version'})
        return jsonify(data=store.write(current_user.user_id,current_user.id,data,request.headers.get('Idempotency-Key'),sid=uuid_id(session_id)))
