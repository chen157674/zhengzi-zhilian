"""七种受控工具契约和有界真实调用回路；正文仅调用栈内存。"""
import json,re
from .deepseek_client import estimate_reservation,MODELS,ProviderError
from .nutrition_estimates import FIELDS
MAX_MODEL_REQUESTS=3
MAX_TOOL_CALLS=8
MAX_TASK_MICRO_CNY=200000

def schema(properties,required=()):return {'type':'object','properties':properties,'required':list(required),'additionalProperties':False}
def string(maximum=200):return {'type':'string','maxLength':maximum}
TOOLS=[
 ('get_training_evidence',schema({'record_id':string(),'expected_version':{'type':'integer','minimum':0}},['record_id','expected_version'])),
 ('find_related_training',schema({'action_id':string(),'days':{'type':'integer','minimum':1,'maximum':30},'limit':{'type':'integer','minimum':1,'maximum':20}},['action_id'])),
 ('search_knowledge',schema({'query':string(1000),'domain':{'type':'string','enum':['movement','nutrition','hypertrophy']},'action_id':string()},['query','domain'])),
 ('get_video_locator',schema({'evidence_id':string(),'expected_version':{'type':'integer','minimum':0}},['evidence_id','expected_version'])),
 ('compare_retest',schema({'task_id':string(),'target_record_id':string()},['task_id','target_record_id'])),
 ('propose_retest',schema({'evidence_id':string()},['evidence_id'])),
 ('estimate_nutrition',schema({'inputs':{'type':'object','properties':{k:({'type':'boolean'} if k=='adult' else {'type':'number'} if k in {'age_years','weight','height'} else {'type':'string'}) for k in FIELDS},'additionalProperties':False},'input_version':{'type':'integer','minimum':0},'items':{'type':'array','items':{'type':'string','enum':['protein','energy']},'minItems':1,'maxItems':2}},['input_version'])),
]
DESCRIPTIONS={'get_training_evidence':'读取本次授权的训练记录实际证据，不能读取任意ID。','find_related_training':'在单独同意后查询本人近30天最多20条同动作有效记录。','search_knowledge':'检索适用的动作、一般饮食或增肌知识，返回可引用ID。','get_video_locator':'将已读取的证据定位到来源记录与时间。','compare_retest':'比较已确认绑定的单项复测任务及其目标。','propose_retest':'为真实issue生成待用户确认的复测草稿，不能自动保存。','estimate_nutrition':'提取自述到inputs并用本地注册公式估算。未知字段省略；weight_unit kg/jin/lb，age_years整数，formula_sex male/female，activity_category light/moderate/vigorous，exercise_status regular/none/unknown，subject self/other/hypothetical。input_version首次为0。'}
TOOL_DEFINITIONS=[{'type':'function','function':{'name':name,'description':DESCRIPTIONS[name],'parameters':spec}} for name,spec in TOOLS]
SPECS=dict(TOOLS)

class CoachLimit(ValueError):pass

def validate(value,spec):
    kind=spec.get('type')
    if kind=='object':
        if not isinstance(value,dict) or set(value)-set(spec['properties']) or set(spec.get('required',[]))-set(value):raise ValueError('工具字段无效')
        for key,item in value.items():validate(item,spec['properties'][key])
    elif kind=='string':
        if not isinstance(value,str) or not value.strip() or len(value)>spec.get('maxLength',200):raise ValueError('工具文字无效')
    elif kind=='integer':
        if type(value) is not int or value<spec.get('minimum',0) or value>spec.get('maximum',2**31-1):raise ValueError('工具数值无效')
    elif kind=='boolean':
        if type(value) is not bool:raise ValueError('工具布尔值无效')
    elif kind=='number':
        import math
        if type(value) not in (int,float) or not math.isfinite(value):raise ValueError('工具数字无效')
    elif kind=='array':
        if not isinstance(value,list) or not spec.get('minItems',0)<=len(value)<=spec.get('maxItems',20):raise ValueError('工具列表无效')
        for item in value:validate(item,spec['items'])
    if 'enum' in spec and value not in spec['enum']:raise ValueError('工具选项无效')

def tool_quote(model,messages,tools=TOOL_DEFINITIONS,maximum=1200):
    # 字节作为保守token上界，同时包含真实历史、工具参数与Schema。
    encoded=json.dumps({'messages':messages,'tools':tools},ensure_ascii=False,allow_nan=False)
    count=len(encoded.encode())+1024
    if count>12000:raise CoachLimit('本次工具上下文过大，请缩小范围')
    return int(count*MODELS[model]['input_micro_per_token']+maximum*MODELS[model]['output_micro_per_token'])

class CoachLoop:
    def __init__(self,provider,execute,before_request,cancelled=lambda:False,on_usage=lambda response:None):
        self.provider=provider;self.execute=execute;self.before_request=before_request;self.cancelled=cancelled;self.on_usage=on_usage
    def run(self,key,messages,model='deepseek-flash',start_turn=0,initial_cost=0,initial_calls=0,maximum=1200,tools=None):
        tools=TOOL_DEFINITIONS if tools is None else tools
        allowed_names={t['function']['name'] for t in tools}
        chain=list(messages);events=[];calls=initial_calls;cost=initial_cost;seen={x['tool_call_id'] for x in chain if x.get('role')=='tool'}
        for turn in range(start_turn,MAX_MODEL_REQUESTS):
            if self.cancelled():return {'status':'cancelled','events':events,'model_requests':turn,'tool_calls':calls,'estimated_micro_cny':cost}
            quote=tool_quote(model,chain,tools=tools,maximum=maximum)
            if cost+quote>MAX_TASK_MICRO_CNY:raise CoachLimit('本次任务费用上限已到')
            self.before_request(turn,quote)
            response=self.provider.tool_completion(key,model,chain,tools,max_output_tokens=maximum,allow_tools=turn<2)
            self.on_usage(response);cost+=response['estimated_micro_cny']
            chain.append(response['message'])
            if not response['calls']:
                return {'status':'ready','content':response['message']['content'],'messages':chain,'events':events,
                        'model_requests':turn+1,'tool_calls':calls,'estimated_micro_cny':cost}
            for call in response['calls']:
                if self.cancelled():return {'status':'cancelled','events':events,'model_requests':turn+1,'tool_calls':calls,'estimated_micro_cny':cost}
                if calls>=MAX_TOOL_CALLS or call['id'] in seen:raise CoachLimit('工具次数或标识限制')
                name=call['name']
                if name not in allowed_names:raise ValueError('非本次作用域白名单工具')
                validate(call['arguments'],SPECS[name]);seen.add(call['id']);calls+=1
                value=self.execute(name,call['arguments'])
                payload=json.dumps(value,ensure_ascii=False,allow_nan=False)
                if len(payload.encode())>24000:raise CoachLimit('工具结果过大，请缩小范围')
                chain.append({'role':'tool','tool_call_id':call['id'],'content':payload})
                events.append({'tool':name,'status':'completed'})
        return {'status':'partial','events':events,'model_requests':3,'tool_calls':calls,'estimated_micro_cny':cost}
