"""本人餐食与明确自述记忆；按账号隔离，来源去重，删除不被旧任务复活。"""
import datetime as dt
import hashlib
import json
import re
from flask import jsonify,request
from flask_login import current_user
from sqlalchemy import select,update
from . import db_schema as s
from .auth import APIError,body
from .database import ConflictError,idempotent,json_text
from .records import integer,uuid_id
from .training_plans import date,ZONE,text
from .nutrition_analysis import GROUPS

MEALS={'breakfast':'早餐','lunch':'午餐','dinner':'晚餐','snack':'加餐','daily':'全天饮食','meal':'一餐'}
OTHER=re.compile(r'朋友|同学|室友|别人|他人|假设|假如|假定|举例|例如|我妈|我爸|我女友|我男友|如果|要是')
FUTURE=re.compile(r'打算|准备吃|计划吃|想吃|想喝|明天|后天|下周|没吃|没喝|没有吃|没有喝|没怎么吃|还没')
FOODS={
    'staple':['米饭','面条','面包','燕麦','红薯','土豆','玉米','馒头','包子','粥','杂粮饭','全麦面包','饺子','粉丝'],
    'protein':['鸡蛋','鸡肉','鸡胸肉','牛肉','猪肉','鱼','虾','豆腐','牛奶','酸奶','豆浆','豆干','蛋白粉'],
    'vegetable':['西兰花','青菜','菠菜','生菜','白菜','胡萝卜','番茄','黄瓜','西红柿','芹菜','蔬菜','蘑菇'],
    'fruit':['苹果','香蕉','橙子','草莓','蓝莓','葡萄','梨','水果','猕猴桃','西瓜'],
    'drink':['奶茶','咖啡','可乐','无糖茶','白水','水','果汁','饮料','茶'],
}

def clean_foods(values):
    if not isinstance(values,list) or not 1<=len(values)<=24:raise APIError('VALIDATION_FAILED','请添加1至24种食物',422)
    result=[]
    for item in values:
        if not isinstance(item,dict) or set(item)-{'name','group','amount'}:raise APIError('VALIDATION_FAILED','食物格式不正确',422)
        name=text(item.get('name'),'name',40);group=item.get('group','other')
        amount=item.get('amount')
        if not isinstance(group,str) or group not in GROUPS:raise APIError('VALIDATION_FAILED','请选择食物分类',422)
        if amount is not None:amount=text(amount,'amount',40)
        if re.search(r'[<>\x00-\x1f]',name+(amount or '')):raise APIError('VALIDATION_FAILED','请填写食物名称和分量',422)
        if not any(x['name']==name for x in result):result.append({'name':name,'group':group,'amount':amount})
    return result

def identify_foods(spoken):
    """只对实际进食片段匹配食物；较长名称优先，避免一杯牛奶被算成水。"""
    found=[];occupied=[]
    from .food_assets import food_catalog
    choices=sorted({(name,group) for group,names in FOODS.items() for name in names}|{(alias,item['group']) for item in food_catalog()['assets'] for alias in item['aliases']},key=lambda x:-len(x[0]))
    for name,group in choices:
        for match in re.finditer(re.escape(name),spoken):
            if any(match.start()<b and match.end()>a for a,b in occupied):continue
            occupied.append(match.span())
            if not any(x['name']==name for x in found):found.append({'name':name,'group':group,'amount':None})
    return found[:24]

class DietJournal:
    def __init__(self,app):
        self.app=app;self.db=app.extensions['database'];self.actor=app.extensions['session_store']._actor
        self.clock=app.config.get('CLOCK') or s.now_ms
    def today(self):return dt.datetime.fromtimestamp(self.clock()/1000,ZONE).date()
    def _row(self,row):
        archived=json.loads(row['analysis_json']) if row['analysis_json'] else {}
        return {k:row[k] for k in ('id','consumed_date','meal_type','summary','source','version','created_at_ms','updated_at_ms')}|{'foods':json.loads(row['foods_json']),'analysis':archived or None,'note':archived.get('user_note',''),'analysis_stale':bool(archived.get('superseded')),'meal_label':MEALS[row['meal_type']]}
    def list(self,uid,auth,start,end):
        left,right=date(start,'date_from'),date(end,'date_to')
        if not 0<=(right-left).days<=92:raise APIError('VALIDATION_FAILED','请选择93天以内的日期范围',422)
        with self.db.transaction(False) as c:
            self.actor(c,uid,auth)
            rows=c.execute(select(s.nutrition_journal).where(s.nutrition_journal.c.user_id==uid,s.nutrition_journal.c.deleted==0,s.nutrition_journal.c.consumed_date>=start,s.nutrition_journal.c.consumed_date<=end).order_by(s.nutrition_journal.c.consumed_date.desc(),s.nutrition_journal.c.created_at_ms.desc())).mappings().all()
        return {'records':[self._row(x) for x in rows],'date_from':start,'date_to':end}
    def get(self,uid,auth,identifier):
        identifier=uuid_id(identifier)
        with self.db.transaction(False) as c:
            self.actor(c,uid,auth)
            row=c.execute(select(s.nutrition_journal).where(s.nutrition_journal.c.id==identifier,s.nutrition_journal.c.user_id==uid,s.nutrition_journal.c.deleted==0)).mappings().first()
            if not row:raise APIError('NOT_FOUND','餐食不存在或已删除',404)
            return self._row(row)
    def receipt(self,uid,auth,key):
        key=uuid_id(key)
        with self.db.transaction(False) as c:
            self.actor(c,uid,auth)
            row=c.execute(select(s.command_receipts).where(s.command_receipts.c.command_id==key,s.command_receipts.c.actor_user_id==uid,s.command_receipts.c.resource_type.like('nutrition.journal.%'))).mappings().first()
            if not row:return {'status':'unconfirmed'}
            result=json.loads(row['result_json']) if row['result_json'] else {}
            return {'status':row['status'],'record_id':result.get('id'),'deleted':bool(result.get('deleted'))}
    def save(self,uid,auth,data,key):
        key=uuid_id(key);day=date(data.get('consumed_date'),'consumed_date');meal=data.get('meal_type')
        if day>self.today():raise APIError('VALIDATION_FAILED','请选择今天或之前的日期',422)
        if not isinstance(meal,str) or meal not in MEALS:raise APIError('VALIDATION_FAILED','请选择餐次',422)
        foods=clean_foods(data.get('foods'));now=self.clock()
        note=self._note(data.get('note',''))
        with self.db.transaction() as c:
            self.actor(c,uid,auth)
            def write():
                identifier=s.new_id()
                c.execute(s.nutrition_journal.insert().values(id=identifier,user_id=uid,source_key='manual:'+key,consumed_date=day.isoformat(),meal_type=meal,foods_json=json_text(foods,list),analysis_json=json_text({'user_note':note}) if note else None,summary='',source='manual',created_at_ms=now,updated_at_ms=now))
                return {'id':identifier}
            result=idempotent(c,uid,key,data,'nutrition.journal.create',write)
            row=c.execute(select(s.nutrition_journal).where(s.nutrition_journal.c.id==result['id'],s.nutrition_journal.c.user_id==uid,s.nutrition_journal.c.deleted==0)).mappings().first()
            if not row:raise APIError('NOT_FOUND','这条餐食已删除',410)
            return self._row(row)
    @staticmethod
    def _note(value):
        if not isinstance(value,str) or len(value)>500 or re.search(r'[<>\x00-\x08\x0b\x0c\x0e-\x1f]',value):
            raise APIError('VALIDATION_FAILED','备注请填写500字以内的普通文字',422)
        return value.strip()
    def edit(self,uid,auth,identifier,data,key):
        identifier=uuid_id(identifier);key=uuid_id(key)
        version=integer(data.get('expected_version'),'expected_version',1)
        with self.db.transaction() as c:
            self.actor(c,uid,auth)
            def write():
                row=c.execute(select(s.nutrition_journal).where(s.nutrition_journal.c.id==identifier,s.nutrition_journal.c.user_id==uid,s.nutrition_journal.c.deleted==0)).mappings().first()
                if not row:raise APIError('NOT_FOUND','餐食不存在或已删除',404)
                if row['version']!=version:raise ConflictError('餐食已在其他页面修改，本地输入已保留')
                day=date(data.get('consumed_date',row['consumed_date']),'consumed_date')
                if day>self.today():raise APIError('VALIDATION_FAILED','请选择今天或之前的日期',422)
                meal=data.get('meal_type',row['meal_type'])
                if not isinstance(meal,str) or meal not in MEALS:raise APIError('VALIDATION_FAILED','请选择餐次',422)
                foods=clean_foods(data.get('foods',json.loads(row['foods_json'])))
                archived=json.loads(row['analysis_json']) if row['analysis_json'] else {}
                note=self._note(data.get('note',archived.get('user_note','')))
                changed=(foods!=json.loads(row['foods_json']) or meal!=row['meal_type'] or day.isoformat()!=row['consumed_date'])
                archived['user_note']=note
                if changed:
                    archived['user_edited']=True
                    if archived.get('result'):archived['superseded']=True
                c.execute(update(s.nutrition_journal).where(s.nutrition_journal.c.id==identifier).values(
                    consumed_date=day.isoformat(),meal_type=meal,foods_json=json_text(foods,list),
                    analysis_json=json_text(archived),version=version+1,updated_at_ms=self.clock()))
                return {'id':identifier}
            result=idempotent(c,uid,key,data,'nutrition.journal.edit',write,identifier)
            row=c.execute(select(s.nutrition_journal).where(s.nutrition_journal.c.id==result['id'],s.nutrition_journal.c.user_id==uid,s.nutrition_journal.c.deleted==0)).mappings().first()
            if not row:raise APIError('NOT_FOUND','餐食已删除',410)
            return self._row(row)
    def remove(self,uid,auth,identifier,data,key):
        identifier=uuid_id(identifier);key=uuid_id(key);version=integer(data.get('expected_version'),'expected_version',1)
        with self.db.transaction() as c:
            self.actor(c,uid,auth)
            def write():
                row=c.execute(select(s.nutrition_journal).where(s.nutrition_journal.c.id==identifier,s.nutrition_journal.c.user_id==uid)).mappings().first()
                if not row:raise APIError('NOT_FOUND','餐食不存在',404)
                if row['version']!=version:raise ConflictError('餐食已更新，请重新读取')
                c.execute(update(s.nutrition_journal).where(s.nutrition_journal.c.id==identifier).values(deleted=1,foods_json='[]',analysis_json=None,summary='',version=version+1,updated_at_ms=self.clock()))
                return {'id':identifier,'deleted':True}
            return idempotent(c,uid,key,data,'nutrition.journal.delete',write,identifier)
    def memories(self,uid,auth):
        with self.db.transaction(False) as c:
            self.actor(c,uid,auth)
            rows=c.execute(select(s.personal_memories).where(s.personal_memories.c.user_id==uid,s.personal_memories.c.deleted==0).order_by(s.personal_memories.c.updated_at_ms.desc(),s.personal_memories.c.id).limit(100)).mappings().all()
        return {'memories':[{k:r[k] for k in ('id','kind','value','evidence','version','updated_at_ms')} for r in rows]}
    def edit_memory(self,uid,auth,identifier,data,remove=False):
        identifier=uuid_id(identifier);version=integer(data.get('expected_version'),'expected_version',1)
        with self.db.transaction() as c:
            self.actor(c,uid,auth)
            row=c.execute(select(s.personal_memories).where(s.personal_memories.c.id==identifier,s.personal_memories.c.user_id==uid)).mappings().first()
            if not row:raise APIError('NOT_FOUND','记忆不存在',404)
            value='' if remove else text(data.get('value'),'value',200)
            if row['version']==version+1 and row['deleted']==int(remove) and row['value']==value:
                return {'id':identifier,'deleted':remove,'version':version+1}
            if row['deleted']:raise APIError('NOT_FOUND','记忆已删除',404)
            if row['version']!=version:raise ConflictError('记忆已更新，请重新读取')
            c.execute(update(s.personal_memories).where(s.personal_memories.c.id==identifier).values(value=value,evidence='' if remove else '本人修改',deleted=int(remove),version=version+1,updated_at_ms=self.clock()))
        return {'id':identifier,'deleted':remove,'version':version+1}
    def context(self,uid,auth):
        memories=self.memories(uid,auth)['memories']
        records=self.list(uid,auth,(self.today()-dt.timedelta(days=6)).isoformat(),self.today().isoformat())['records']
        return {'personal_statements':[{'kind':x['kind'],'value':x['value']} for x in memories[:30]],
                'recent_foods':[{'date':x['consumed_date'],'meal':x['meal_label'],'foods':[f['name'] for f in x['foods']]} for x in records[:14]]}
    def remember(self,uid,auth,source_id,spoken,analysis=None,summary='',analysis_kind=None,root_id=None,references=None):
        """仅从本人明确表达提取事实；模型建议不进入个人记忆。"""
        if not isinstance(spoken,str) or OTHER.search(spoken):return {'saved':False}
        now=self.clock();day=self.today();facts=[]
        for match in re.finditer(r'我(?:平时|比较|最|很|特别|一直|也)?(?P<kind>不喜欢吃|不爱吃|不吃|喜欢吃|爱吃|喜欢喝|不喜欢喝|不喝)(?P<foods>[^，。！？；\n?？]{1,80})',spoken):
            kind='food_dislike' if match['kind'].startswith('不') else 'food_like'
            names=identify_foods(match['foods'])
            if not names and ('吃' in match['kind'] or '喝' in match['kind']) and not re.search(r'吗|什么|怎么|可以|因为|的话|[?？]',match['foods']):
                names=[{'name':x.strip(),'group':'other'} for x in re.split(r'、|和|以及',match['foods']) if 1<=len(x.strip())<=30]
            for food in names:facts.append((kind,food['name'],match[0][:200],food['name']))
        for pattern,key,label in [(r'我(?:的)?(?:现在)?体重(?:是|为|有)?\s*(\d{2,3}(?:\.\d{1,2})?)\s*(公斤|千克|kg|斤)', 'weight','体重'),
                                  (r'我(?:的)?身高(?:是|为|有)?\s*(\d{3}(?:\.\d)?)\s*(厘米|cm|公分)', 'height','身高'),
                                  (r'我(?:今年)?\s*(\d{1,2})\s*岁','age','年龄')]:
            match=re.search(pattern,spoken,re.I)
            if match:facts.append(('body',label+' '+match[1]+('岁' if key=='age' else match[2]),match[0],key))
        for match in re.finditer(r'我(?:的目标是|想要|想|要)(增肌|减脂|减重|保持体重|增强体能)',spoken):
            facts.append(('goal',match[1],match[0],'goal'))
        consumption=re.search(r'(?:我)?(?:今天|昨天|早上|中午|晚上|早餐|午餐|晚餐|刚才|刚刚)?[^。！？\n]{0,20}?(?:吃了|喝了|吃的是|喝的是|早餐是|午餐是|晚餐是)([^。！？\n]+)',spoken)
        foods=None;meal='meal'
        if analysis and not FUTURE.search(spoken):
            # 一餐/全天分析的输入自身就是用户提交的饮食自述，食物已逐项原文校验。
            foods=clean_foods([{k:x.get(k) for k in ('name','group','amount')} for x in analysis['foods']])
        elif consumption and not FUTURE.search(consumption[0]):
            foods=identify_foods(consumption[1])
            if not foods:
                foods=[{'name':x.strip(),'group':'other','amount':None} for x in re.split(r'、|，|,|和|以及',consumption[1]) if 1<=len(x.strip())<=40 and not re.search(r'什么|怎么|吗|[?？]',x)][:24]
        if foods:
            if '昨天' in spoken:day-=dt.timedelta(days=1)
            explicit=re.search(r'\b(\d{4}-\d{2}-\d{2})\b',spoken)
            if explicit:
                try:day=date(explicit[1])
                except APIError:foods=None
            if day>self.today():foods=None
            meal='daily' if analysis_kind=='day' else next((k for k,words in [('breakfast',('早餐','早饭','早上')),('lunch',('午餐','午饭','中午')),('dinner',('晚餐','晚饭','晚上')),('snack',('加餐',))] if any(w in spoken for w in words)),'meal')
        saved_id=None;remembered_root=root_id or source_id;saved_facts=0
        archived=json_text({'result':analysis,'references':references or []}) if analysis else None
        with self.db.transaction() as c:
            self.actor(c,uid,auth)
            for kind,value,evidence,identity in facts:
                value_key=hashlib.sha256(identity.encode()).hexdigest()
                old=c.execute(select(s.personal_memories).where(s.personal_memories.c.user_id==uid,s.personal_memories.c.kind==kind,s.personal_memories.c.value_key==value_key)).mappings().first()
                if old and old['deleted']:continue
                if kind in {'food_like','food_dislike'} and not old:
                    opposite=c.execute(select(s.personal_memories).where(s.personal_memories.c.user_id==uid,s.personal_memories.c.kind==('food_like' if kind=='food_dislike' else 'food_dislike'),s.personal_memories.c.value_key==value_key,s.personal_memories.c.deleted==0)).mappings().first()
                    if opposite:
                        c.execute(update(s.personal_memories).where(s.personal_memories.c.id==opposite['id']).values(kind=kind,value=value,evidence=evidence,version=opposite['version']+1,updated_at_ms=now))
                        saved_facts+=1
                        continue
                if old:
                    if old['evidence']!='本人修改':
                        c.execute(update(s.personal_memories).where(s.personal_memories.c.id==old['id']).values(value=value,evidence=evidence,updated_at_ms=now,version=old['version']+int(value!=old['value'])))
                        saved_facts+=1
                else:
                    c.execute(s.personal_memories.insert().values(user_id=uid,kind=kind,value_key=value_key,value=value,evidence=evidence,created_at_ms=now,updated_at_ms=now))
                    saved_facts+=1
            if foods:
                source='ai:'+str(root_id or source_id)
                old=c.execute(select(s.nutrition_journal).where(s.nutrition_journal.c.user_id==uid,s.nutrition_journal.c.source_key==source)).mappings().first()
                if not old:
                    duplicate=c.execute(select(s.nutrition_journal).where(s.nutrition_journal.c.user_id==uid,s.nutrition_journal.c.source=='ai',s.nutrition_journal.c.deleted==0,s.nutrition_journal.c.consumed_date==day.isoformat(),s.nutrition_journal.c.meal_type==meal,s.nutrition_journal.c.foods_json==json_text(foods,list))).mappings().first()
                    if duplicate:old=duplicate;remembered_root=duplicate['source_key'].removeprefix('ai:')
                if old and not old['deleted']:
                    if old['analysis_json'] and json.loads(old['analysis_json']).get('user_edited'):
                        saved_id=old['id']
                        continue_update=False
                    else:continue_update=True
                    saved_id=old['id']
                    if continue_update:
                        previous=json.loads(old['analysis_json']) if old['analysis_json'] else {}
                        current=json.loads(archived) if archived else previous
                        if previous.get('user_note'):current['user_note']=previous['user_note']
                        c.execute(update(s.nutrition_journal).where(s.nutrition_journal.c.id==saved_id).values(foods_json=json_text(foods,list),analysis_json=json_text(current) if current else None,summary=summary[:500] if analysis else old['summary'],consumed_date=day.isoformat(),meal_type=meal,version=old['version']+1,updated_at_ms=now))
                elif not old:
                    saved_id=s.new_id()
                    c.execute(s.nutrition_journal.insert().values(id=saved_id,user_id=uid,source_key=source,consumed_date=day.isoformat(),meal_type=meal,foods_json=json_text(foods,list),analysis_json=archived,summary=summary[:500],source='ai',created_at_ms=now,updated_at_ms=now))
        return {'saved':bool(saved_id or saved_facts),'journal_id':saved_id,'root_id':remembered_root}

def register_diet_journal(app):
    store=DietJournal(app);app.extensions['diet_journal']=store
    @app.route('/api/v1/nutrition/journal',methods=['GET','POST'])
    def nutrition_journal():
        uid,auth=current_user.user_id,current_user.id
        if request.method=='GET':
            if set(request.args)-{'date_from','date_to'}:raise APIError('INVALID_REQUEST','日期查询格式不正确',400)
            today=store.today().isoformat()
            return jsonify(data=store.list(uid,auth,request.args.get('date_from',today),request.args.get('date_to',today)))
        return jsonify(data=store.save(uid,auth,body({'consumed_date','meal_type','foods','note'}),request.headers.get('Idempotency-Key'))),201
    @app.patch('/api/v1/nutrition/journal/<identifier>')
    def nutrition_journal_edit(identifier):
        return jsonify(data=store.edit(current_user.user_id,current_user.id,identifier,body({'expected_version','consumed_date','meal_type','foods','note'}),request.headers.get('Idempotency-Key')))
    @app.get('/api/v1/nutrition/journal/<identifier>')
    def nutrition_journal_detail(identifier):
        return jsonify(data=store.get(current_user.user_id,current_user.id,identifier))
    @app.get('/api/v1/nutrition/journal/commands/<key>')
    def nutrition_journal_receipt(key):
        return jsonify(data=store.receipt(current_user.user_id,current_user.id,key))
    @app.delete('/api/v1/nutrition/journal/<identifier>')
    def nutrition_journal_delete(identifier):
        return jsonify(data=store.remove(current_user.user_id,current_user.id,identifier,body({'expected_version'}),request.headers.get('Idempotency-Key')))
    @app.get('/api/v1/account/memories')
    def personal_memories():
        return jsonify(data=store.memories(current_user.user_id,current_user.id))
    @app.route('/api/v1/account/memories/<identifier>',methods=['PATCH','DELETE'])
    def personal_memory_edit(identifier):
        remove=request.method=='DELETE'
        return jsonify(data=store.edit_memory(current_user.user_id,current_user.id,identifier,body({'expected_version'} if remove else {'expected_version','value'}),remove))
