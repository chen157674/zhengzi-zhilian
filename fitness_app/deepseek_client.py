"""只访问DeepSeek官方HTTPS接口；无重定向、无隐式重试、不记录请求正文/Key。"""
import json,math,re,socket,time,urllib.request,urllib.error
from decimal import Decimal,ROUND_CEILING
from .ai_vision import input_budget

BASE_URL='https://api.deepseek.com'
# 2026-10-23官方最高价/缓存命中价；金额仅作本应用估算，非供应商账单。
MODELS={
    'deepseek-flash':{'label':'快速解读（支持看图）','input_micro_per_token':Decimal('2'),'cached_micro_per_token':Decimal('.04'),'output_micro_per_token':Decimal('8')},
    'deepseek-v4-pro':{'label':'深入复盘','input_micro_per_token':Decimal('9'),'cached_micro_per_token':Decimal('.225'),'output_micro_per_token':Decimal('27')},
}
PRICING_VERSION='deepseek-official-peak-20261023'


def streaming_summary(content):
    """只提取顶层首字段的已解码文字，不向页面暴露JSON片段。"""
    match=re.match(r'^\s*\{\s*"summary"\s*:\s*',content)
    if not match:return ''
    value=content[match.end():]
    if not value.startswith('"'):return ''
    try:
        text,_=json.JSONDecoder().raw_decode(value)
    except (ValueError,json.JSONDecodeError):
        text=''
        # 一个SSE片段可能正好停在反斜线或Unicode转义内部。
        for cut in range(min(7,len(value))):
            candidate=value[:len(value)-cut] if cut else value
            try:
                text=json.loads(candidate+'"');break
            except (ValueError,json.JSONDecodeError):continue
    if not isinstance(text,str):return ''
    return text.encode('utf-8',errors='ignore').decode('utf-8')[:600]


class ProviderError(RuntimeError):
    def __init__(self,code,message,retryable=False,uncertain_charge=False,usage=None,cost=None):
        super().__init__(message);self.code=code;self.retryable=retryable;self.uncertain_charge=uncertain_charge
        self.usage=usage;self.estimated_micro_cny=cost


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


def validate_key(value):
    if not isinstance(value,str):raise ValueError('密钥格式无效')
    value=value.strip()
    if not 16<=len(value)<=1024 or not value.isascii() or any(ord(ch)<=32 or ord(ch)==127 for ch in value):
        raise ValueError('密钥应为不含空白的API Key，不要填写Bearer前缀')
    return value


def estimate_reservation(model,messages,max_output_tokens):
    # 文字、图片分别估算，再为协议标记预留空间。
    count,_=input_budget(messages);count+=1024
    rates=MODELS[model]
    return int((count*rates['input_micro_per_token']+max_output_tokens*rates['output_micro_per_token']).to_integral_value(rounding=ROUND_CEILING))


def estimated_cost(model,usage):
    rates=MODELS[model];prompt=usage['prompt_tokens'];output=usage['completion_tokens']
    hit=usage.get('prompt_cache_hit_tokens',0)
    if type(hit) is not int or not 0<=hit<=prompt:hit=0
    return int(((prompt-hit)*rates['input_micro_per_token']+hit*rates['cached_micro_per_token']+
                output*rates['output_micro_per_token']).to_integral_value(rounding=ROUND_CEILING))


def response_usage(source,model):
    if not isinstance(source,dict):raise ValueError('usage')
    usage={}
    for field in ('prompt_tokens','completion_tokens'):
        if type(source.get(field)) is not int or not 0<=source[field]<=2**31:raise ValueError('usage')
        usage[field]=source[field]
    for field in ('total_tokens','prompt_cache_hit_tokens','prompt_cache_miss_tokens'):
        if type(source.get(field)) is int and 0<=source[field]<=2**31:usage[field]=source[field]
    usage.setdefault('total_tokens',usage['prompt_tokens']+usage['completion_tokens'])
    return usage,estimated_cost(model,usage)


class DeepSeekClient:
    def __init__(self,timeout=45,opener=None):
        self.timeout=timeout;self.opener=opener or urllib.request.build_opener(NoRedirect())

    def _request(self,key,path,payload=None):
        key=validate_key(key)
        encoded=None if payload is None else json.dumps(payload,ensure_ascii=False,allow_nan=False).encode('utf-8')
        request=urllib.request.Request(BASE_URL+path,data=encoded,method='GET' if encoded is None else 'POST',
                    headers={'Authorization':'Bearer '+key,'Content-Type':'application/json','Accept':'application/json'})
        try:
            with self.opener.open(request,timeout=self.timeout) as response:
                if response.geturl().split('?',1)[0]!=BASE_URL+path:
                    raise ProviderError('AI_REDIRECT_REJECTED','AI接口地址发生变化，已停止请求')
                raw=response.read(2*1024*1024+1)
                if len(raw)>2*1024*1024:raise ProviderError('AI_RESPONSE_INVALID','AI响应过大',uncertain_charge=payload is not None)
                result=json.loads(raw.decode('utf-8'))
                if not isinstance(result,dict):raise ValueError('object required')
                return result
        except urllib.error.HTTPError as error:
            # 不返回/记录可能包含敏感内容的上游错误正文。
            code=error.code;error.close()
            mapped={401:('AI_KEY_INVALID','AI密钥无效或已失效'),402:('AI_BALANCE_INSUFFICIENT','AI账户余额不足'),
                    429:('AI_RATE_LIMITED','AI服务限流，请稍后重试'),400:('AI_REQUEST_REJECTED','AI服务未接受本次请求')}
            name,message=mapped.get(code,('AI_PROVIDER_UNAVAILABLE','AI服务暂不可用'))
            raise ProviderError(name,message,code==429 or code>=500,payload is not None and code>=500) from None
        except (urllib.error.URLError,TimeoutError,socket.timeout,ConnectionError,OSError):
            raise ProviderError('AI_NETWORK_UNCERTAIN','AI请求未能确认，请检查请求状态后重试',True,payload is not None) from None
        except (json.JSONDecodeError,UnicodeError,ValueError,KeyError,IndexError,TypeError):
            raise ProviderError('AI_RESPONSE_INVALID','AI响应格式无效，未生成有效报告',uncertain_charge=payload is not None) from None

    def test(self,key):
        data=self._request(key,'/models').get('data')
        if not isinstance(data,list):raise ProviderError('AI_RESPONSE_INVALID','模型列表无效')
        names=[m.get('id') for m in data if isinstance(m,dict) and m.get('id') in MODELS]
        return {'connection_verified':True,'generation_verified':False,'available_models':names}

    def generate(self,key,model,messages,max_output_tokens=1800):
        if model not in MODELS:raise ValueError('不支持的AI模型')
        if type(max_output_tokens) is not int or not 1<=max_output_tokens<=8192:raise ValueError('输出上限无效')
        _,images=input_budget(messages)
        if images and model!='deepseek-flash':raise ValueError('当前模型不支持画面理解')
        data=self._request(key,'/chat/completions',{'model':model,'messages':messages,'max_tokens':max_output_tokens,
                          'thinking':{'type':'disabled'},'response_format':{'type':'json_object'},'stream':False})
        return self._generation_result(data,key,model)

    def _generation_result(self,data,key,model):
        usage=None;cost=None
        try:
            usage,cost=response_usage(data['usage'],model)
            choice=data['choices'][0]
            if choice['finish_reason']!='stop':raise ProviderError('AI_OUTPUT_INCOMPLETE','AI回复未完整生成，不能作为有效报告',usage=usage,cost=cost)
            content=choice['message']['content']
            if not isinstance(content,str) or not content.strip():raise ValueError('empty')
            result=json.loads(content)
            if not isinstance(result,dict):raise ValueError('object')
        except (KeyError,IndexError,TypeError,ValueError,json.JSONDecodeError):
            raise ProviderError('AI_RESPONSE_INVALID','AI回复或用量格式无效，未生成有效报告',uncertain_charge=cost is None,usage=usage if cost is not None else None,cost=cost) from None
        original_id=str(data.get('id',''));upstream=original_id[:200]
        if key in original_id or 'sk-' in upstream:upstream=''
        return {'result':result,'usage':usage,'upstream_request_id':upstream,
                'estimated_micro_cny':cost,'pricing_version':PRICING_VERSION}

    def generate_stream(self,key,model,messages,max_output_tokens=1800,on_summary=None):
        """接收官方SSE；最终仍按原协议核对完成标记、JSON和用量。"""
        if model not in MODELS:raise ValueError('不支持的AI模型')
        if type(max_output_tokens) is not int or not 1<=max_output_tokens<=8192:raise ValueError('输出上限无效')
        _,images=input_budget(messages)
        if images and model!='deepseek-flash':raise ValueError('当前模型不支持画面理解')
        key=validate_key(key)
        payload={'model':model,'messages':messages,'max_tokens':max_output_tokens,'thinking':{'type':'disabled'},
                 'response_format':{'type':'json_object'},'stream':True,'stream_options':{'include_usage':True}}
        req=urllib.request.Request(BASE_URL+'/chat/completions',data=json.dumps(payload,ensure_ascii=False,allow_nan=False).encode('utf-8'),
            headers={'Authorization':'Bearer '+key,'Content-Type':'application/json','Accept':'text/event-stream'},method='POST')
        pieces=[];usage=None;finish=None;upstream='';done=False;size=0;previous='';event=[]
        deadline=time.monotonic()+max(15,self.timeout*2)
        try:
            with self.opener.open(req,timeout=self.timeout) as response:
                if response.geturl().split('?',1)[0]!=BASE_URL+'/chat/completions':raise ProviderError('AI_REDIRECT_REJECTED','AI接口地址发生变化，已停止请求',uncertain_charge=True)
                while True:
                    if time.monotonic()>deadline:raise TimeoutError()
                    line=response.readline(65537)
                    if not line:break
                    size+=len(line)
                    if size>2*1024*1024 or len(line)>65536:raise ValueError('stream too large')
                    line=line.decode('utf-8').rstrip('\r\n')
                    if line.startswith('data:'):event.append(line[5:].lstrip());continue
                    if line or not event:continue
                    raw='\n'.join(event);event=[]
                    if raw=='[DONE]':done=True;break
                    item=json.loads(raw)
                    if not isinstance(item,dict) or item.get('error'):raise ValueError('invalid stream')
                    if isinstance(item.get('usage'),dict):usage=item['usage']
                    if isinstance(item.get('id'),str):upstream=item['id']
                    choices=item.get('choices',[])
                    if not isinstance(choices,list):raise ValueError('choices')
                    if choices:
                        choice=choices[0]
                        if choice.get('finish_reason') is not None:finish=choice['finish_reason']
                        text=(choice.get('delta') or {}).get('content')
                        if text is not None:
                            if not isinstance(text,str):raise ValueError('content')
                            pieces.append(text)
                            summary=streaming_summary(''.join(pieces))
                            if summary and summary!=previous:
                                previous=summary
                                if on_summary:on_summary(summary)
        except urllib.error.HTTPError as error:
            status=error.code;error.close()
            name,message={401:('AI_KEY_INVALID','AI密钥无效或已失效'),402:('AI_BALANCE_INSUFFICIENT','AI账户余额不足'),
                429:('AI_RATE_LIMITED','AI服务限流，请稍后重试'),400:('AI_REQUEST_REJECTED','AI服务未接受本次请求')}.get(status,('AI_PROVIDER_UNAVAILABLE','AI服务暂不可用'))
            raise ProviderError(name,message,status==429 or status>=500,status>=500) from None
        except ProviderError:raise
        except (urllib.error.URLError,TimeoutError,socket.timeout,ConnectionError,OSError):
            try:known,cost=response_usage(usage,model)
            except (ValueError,TypeError,KeyError):known=cost=None
            raise ProviderError('AI_NETWORK_UNCERTAIN','AI连接中断，请核对请求状态后重试',True,cost is None,usage=known,cost=cost) from None
        except (ValueError,TypeError,KeyError,UnicodeError):
            try:known,cost=response_usage(usage,model)
            except (ValueError,TypeError,KeyError):known=cost=None
            raise ProviderError('AI_RESPONSE_INVALID','AI回复格式无效',uncertain_charge=cost is None,usage=known,cost=cost) from None
        data={'id':upstream,'usage':usage,'choices':[{'finish_reason':finish if done else 'incomplete','message':{'content':''.join(pieces)}}]}
        return self._generation_result(data,key,model)
