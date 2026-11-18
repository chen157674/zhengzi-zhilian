"""仅显式保存本人当前字段；回执无正文，无历史估算快照。"""
import json
from flask import jsonify,request
from flask_login import current_user
from sqlalchemy import select,update,delete
from . import db_schema as s
from .auth import body,APIError
from .database import ConflictError,json_text
from .records import integer
from .nutrition_estimates import normalize_inputs,estimate,NutritionInputError

class NutritionProfiles:
    def __init__(self,app):self.app=app;self.db=app.extensions['database'];self.sessions=app.extensions['session_store'];self.clock=app.config.get('CLOCK') or s.now_ms
    def get(self,uid,auth):
        with self.db.transaction(False) as c:
            self.sessions._actor(c,uid,auth);row=c.execute(select(s.nutrition_profiles).where(s.nutrition_profiles.c.user_id==uid)).mappings().first()
            return {'version':row['version'] if row else 0,'inputs':json.loads(row['data_json']) if row else {},'updated_at_ms':row['updated_at_ms'] if row else None}
    def coach_context(self,uid,auth):
        """本人资料在服务端合并和计算，不接受客户端提供的营养数字。"""
        training=self.app.extensions['training_info'].get_profile(uid,auth)
        nutrition=self.get(uid,auth)
        inputs={'subject':'self'}
        for source,target,unit in [('age_years','age_years',None),('height_cm','height','cm'),('weight_kg','weight','kg')]:
            if training.get(source) is not None:
                inputs[target]=training[source]
                if unit:inputs[target+'_unit']=unit
        inputs.update(nutrition['inputs'])
        calculation=estimate(inputs)
        items=[{key:item[key] for key in ('item','status','display_lower','display_upper','unit','method_id','missing') if key in item} for item in calculation['items']]
        return {'training_version':training['version'],'nutrition_version':nutrition['version'],
                'training_profile':{key:training[key] for key in ('age_years','height_cm','weight_kg','goals','experience','available_minutes') if training.get(key) not in (None,[])},
                'inputs':inputs,'items':items,'has_training_profile':any(training.get(key) not in (None,[]) for key in ('age_years','height_cm','weight_kg','goals','experience')),
                'has_nutrition_profile':bool(nutrition['inputs']),
                'calculated_items':sum(item['status']=='calculated' for item in items)}

    def write(self,uid,auth,data,clear=False):
        version=integer(data.get('expected_version'),'expected_version')
        values={} if clear else data.get('inputs')
        try:normalized=normalize_inputs(values)
        except NutritionInputError as error:raise APIError(error.code,str(error),422) from None
        if not clear and normalized.get('subject')!='self':raise APIError('NUTRITION_PROFILE_SUBJECT','只能保存本人的资料',422)
        # 保存规范化的表单字段，单位明确，下次仍由用户主动复用。
        saved={k:v for k,v in normalized.items() if k not in {'weight_kg','height_cm'}}
        if 'weight_kg' in normalized:saved.update(weight=normalized['weight_kg'],weight_unit='kg')
        if 'height_cm' in normalized:saved.update(height=normalized['height_cm'],height_unit='cm')
        with self.db.transaction() as c:
            self.sessions._actor(c,uid,auth);row=c.execute(select(s.nutrition_profiles).where(s.nutrition_profiles.c.user_id==uid)).mappings().first()
            current=row['version'] if row else 0
            if current!=version:
                if row and json.loads(row['data_json'])==saved and current==version+1:return self._result(row)
                raise ConflictError('身体资料已更新，请重新读取','PROFILE_VERSION_CONFLICT')
            val={'data_json':json_text(saved),'version':current+1,'updated_at_ms':self.clock()}
            if row:c.execute(update(s.nutrition_profiles).where(s.nutrition_profiles.c.user_id==uid).values(**val))
            else:c.execute(s.nutrition_profiles.insert().values(user_id=uid,**val))
            return self._result(val)
    def _result(self,row):return {'version':row['version'],'inputs':json.loads(row['data_json']),'updated_at_ms':row['updated_at_ms']}

def register_nutrition_profiles(app):
    store=NutritionProfiles(app);app.extensions['nutrition_profiles']=store
    @app.get('/api/v1/nutrition/coach-context')
    def nutrition_coach_context():
        context=store.coach_context(current_user.user_id,current_user.id)
        return jsonify(data={key:context[key] for key in ('training_version','nutrition_version','has_training_profile','has_nutrition_profile','calculated_items')})

    @app.route('/api/v1/nutrition/profile',methods=['GET','PUT','DELETE'])
    def nutrition_profile():
        uid,auth=current_user.user_id,current_user.id
        if request.method=='GET':return jsonify(data=store.get(uid,auth))
        data=body({'expected_version'} if request.method=='DELETE' else {'expected_version','inputs'})
        result=store.write(uid,auth,data,request.method=='DELETE')
        jobs=app.extensions.get('coach_tasks')
        if jobs:jobs.invalidate_owner(uid,mode='nutrition')
        return jsonify(data=result)
