"""隔离浏览器用确定性供应商协议替身；真实任务/账本/校验/保存。"""
import os,json
from fitness_app import app as app_module
from fitness_app.knowledge_store import KnowledgeStore
from fitness_app.runtime import serve
class Provider:
    def tool_completion(self,key,model,messages,tools,**kwargs):
        raw=messages[-1]['content']
        if 'conditions' in raw:
            data=json.loads(raw);conditions=data['conditions'];source=next(x for x in data['sources'] if x['action_id']=='bodyweight_squat')
            output={'title':'AI协议草稿','weekly_sessions':[{'weekday':d,'title':'徒手训练','estimated_minutes':30,'items':[{'action_id':'bodyweight_squat','target_sets':1,'target_reps':3,'target_duration_ms':None,'rest_seconds':30}]} for d in conditions['constraints']['training_weekdays']],'knowledge_ids':[source['id']]}
        elif 'facts' in raw:
            facts=json.loads(raw)['facts'];output={'summary':'本周可先核对已保存的动作与执行记录，再安排下一次。','focus':['按真实观察选择关注项目。'],'evidence_refs':[x['record_id'] for x in facts['records'][:1]],'knowledge_refs':[]}
        else:
            content=next(x['content'] for x in messages if '服务器本次实际检索结果' in x.get('content',''));rows=json.loads(content.split('：',1)[1].split('\n本次主动填写：',1)[0]);output={'summary':rows[0]['text'],'knowledge_ids':[rows[0]['id']],'evidence_ids':[],'focus_evidence_id':None,'question':None}
        return {'message':{'role':'assistant','content':json.dumps(output,ensure_ascii=False)},'calls':[],'usage':{'prompt_tokens':20,'completion_tokens':20,'total_tokens':40},'estimated_micro_cny':100}
original=app_module.create_app
def create(config):
    app=original({**config,'TESTING':True,'DATABASE_PATH':os.environ['FITNESS_DATABASE_PATH']})
    app.extensions['knowledge_store']=KnowledgeStore(index_dir='.tmp/nonexistent-knowledge-index')
    app.extensions['coach_provider_factory']=Provider;app.extensions['training_provider_factory']=Provider
    return app
app_module.create_app=create
serve(0)
