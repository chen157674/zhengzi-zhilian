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
        if any('你正在执行真实饮食记录分析' in m.get('content','') for m in messages):
            content['summary']='米饭搭配鸡蛋，下一餐再选一道青菜，奶茶可以换成无糖茶。'
            content['analysis']={'foods':[{'name':'米饭','evidence':'一碗米饭','group':'staple','amount':'一碗'},{'name':'鸡蛋','evidence':'两个鸡蛋','group':'protein','amount':'两个'},{'name':'奶茶','evidence':'一杯奶茶','group':'drink','amount':'一杯'}],
                'next_meal':'米饭配豆腐和炒青菜，按食欲调整。','swaps':[{'from':'奶茶','to':'白水或无糖茶','reason':'减少含糖饮料的频率。'}]}
            record=next((m['content'] for m in messages if '原始记录：' in m.get('content','')),'')
            if '一碗泡面' in record and '一个汉堡' in record and '一份牛肉' in record:
                content['summary']='泡面少放调料，汉堡少搭薯条和甜饮；牛肉选瘦肉，并轮换鱼和豆类。'
                content['knowledge_ids']=[r['id'] for r in rows]
                content['analysis']={'foods':[{'name':'泡面','evidence':'一碗泡面','group':'staple','amount':'一碗'},{'name':'汉堡','evidence':'一个汉堡','group':'other','amount':'一个'},{'name':'牛肉','evidence':'一份牛肉','group':'protein','amount':'一份'}],
                    'next_meal':'米饭配清蒸鱼或豆腐，再搭一道青菜。',
                    'swaps':[{'from':'泡面','to':'米饭配鸡蛋和青菜','reason':'轮换主食，搭配蛋白质食物与蔬菜。'},{'from':'牛肉','to':'鱼或豆腐','reason':'在一周内轮换蛋白质来源。'}]}
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
