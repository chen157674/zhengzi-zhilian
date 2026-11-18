"""本地临时估算入口；身份与CSRF沿原认证，无正文持久化。"""
from flask import jsonify,request
from .auth import body,APIError
from .nutrition_estimates import estimate,NutritionInputError

def register_nutrition(app):
    @app.get('/api/v1/nutrition/meal-options')
    def nutrition_meal_options():
        if set(request.args)-{'meal_type','scene'}:
            raise APIError('INVALID_REQUEST','餐食选择参数无效',400)
        from .meal_recommendations import options
        return jsonify(data=options(request.args.get('meal_type','lunch'), request.args.get('scene','home')))

    from .knowledge_store import KnowledgeStore
    store=KnowledgeStore();app.extensions['knowledge_store']=store;app.extensions.setdefault('stop_hooks',[]).append(store.stop)

    @app.get('/api/v1/nutrition/food-assets')
    def nutrition_food_assets():
        from .food_assets import published_food_assets
        return jsonify(data=published_food_assets())

    @app.post('/api/v1/nutrition/estimate')
    def nutrition_estimate():
        data=body({'inputs','input_version','items'},max_bytes=8192)
        try:result=estimate(data.get('inputs'),data.get('input_version',0),data.get('items'))
        except NutritionInputError as error:
            raise APIError(error.code,str(error),422,{error.field:'请检查填写内容及单位'}) from None
        return jsonify(data=result)

    @app.post('/api/v1/knowledge/search')
    def knowledge_search():
        store=app.extensions['knowledge_store']
        data=body({'query','domain','action_id','limit'},max_bytes=8192)
        try:result=store.retrieval(data.get('query'),data.get('domain'),data.get('action_id'),data.get('limit',5))
        except ValueError:raise APIError('VALIDATION_FAILED','请检查知识主题和问题',422) from None
        from .learning_guides import related_tutorial
        result['tutorial']=related_tutorial(store,result['matches'])
        return jsonify(data=result)

    @app.get('/api/v1/knowledge/chunks/<chunk_id>')
    def knowledge_chunk(chunk_id):
        store=app.extensions['knowledge_store']
        if set(request.args)-{'version'}:raise APIError('INVALID_REQUEST','来源查询字段无效',400)
        item=store.published(chunk_id,request.args.get('version'))
        if not item:raise APIError('NOT_FOUND','知识条目不可用',404)
        if item['status']!='published':
            return jsonify(error={'code':'KNOWLEDGE_WITHDRAWN' if item['status']=='withdrawn' else 'KNOWLEDGE_VERSION_UNAVAILABLE','message':'该版本来源已不可用于新建议','fields':{}},data=item),410
        return jsonify(data=item)

    @app.get('/api/v1/knowledge/catalog')
    def knowledge_catalog():
        store=app.extensions['knowledge_store']
        if request.args:raise APIError('INVALID_REQUEST','知识目录不接受额外参数',400)
        return jsonify(data=store.catalog())

    @app.get('/api/v1/actions/<action_id>/guide')
    def action_guide(action_id):
        store=app.extensions['knowledge_store']
        from .catalogue import get_action
        from .posture_rules import rules_for
        from .framing_guide import guide
        from .presentation_assets import cover_catalogue
        from urllib.parse import urlencode
        action=get_action(action_id)
        if not action:raise APIError('NOT_FOUND','动作不存在',404)
        rows=[store.public_row(r) for r in store.by_id.values() if r.get('status')=='published' and r['action_id']==action_id]
        rules=rules_for(action_id)
        from .learning_guides import tutorial_for
        tutorial=tutorial_for(store,action_id)
        return jsonify(data={'action':{'id':action_id,'name':action['name'],'equipment':action.get('equipment'),'unit':action['unit'],'cover':cover_catalogue().get(action_id)},
            'capabilities':{'recognition':{'method':action.get('recognition_method'),'verification_status':action.get('recognition_validation','not_independently_validated')},'counting':action.get('counting_available',False),'posture_items':rules,
                            'verification_status':action.get('validation_status','not_independently_validated'),'capability_version':action['capability_version']},
            'tutorial':tutorial,'view_guidance':guide(action_id=action_id),'knowledge':{'coverage':'available' if rows else 'limited','status':store.index_status,'chunks':rows},
            'sources':list({r['source_id']:r['source'] for r in rows}.values()),
            'links':{'realtime':'/realtime?'+urlencode({'action':action_id}),'video':'/video?'+urlencode({'action':action_id}),'knowledge':'/coach/knowledge?'+urlencode({'action_id':action_id})}})
