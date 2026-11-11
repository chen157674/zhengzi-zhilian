"""真实本地关键词检索用于UI检查，不加载embedding模型或供应商。"""
import os,json
from fitness_app import app as app_module
from fitness_app.knowledge_store import KnowledgeStore
from fitness_app.runtime import serve
class Provider:
    def tool_completion(self,key,model,messages,tools,**kwargs):
        source=next(x['content'] for x in messages if '服务器本次实际检索结果' in x.get('content',''))
        rows=json.loads(source.split('：',1)[1])
        content={'summary':rows[0]['text'],'knowledge_ids':[rows[0]['id']],'evidence_ids':[],'focus_evidence_id':None,'question':None}
        return {'message':{'role':'assistant','content':json.dumps(content,ensure_ascii=False)},'calls':[],
                'usage':{'prompt_tokens':20,'completion_tokens':20,'total_tokens':40},'estimated_micro_cny':10}
original=app_module.create_app
def create(config):
    app=original({**config,'TESTING':True,'DATABASE_PATH':os.environ['FITNESS_DATABASE_PATH']})
    store=KnowledgeStore(index_dir='.tmp/nonexistent-knowledge-index')
    app.extensions['knowledge_store']=store
    app.extensions['coach_provider_factory']=Provider
    return app
app_module.create_app=create
serve(0)
