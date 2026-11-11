"""官方API协议/秘密保护和计费估算；HTTP替身，不声称真实DeepSeek通过。"""
import io,json,unittest,urllib.error
from fitness_app.deepseek_client import DeepSeekClient,ProviderError,NoRedirect,estimate_reservation,estimated_cost,streaming_summary

KEY='sk-protocol-only-fixture-123456789'


class Response:
    def __init__(self,body,url):self.body=body;self.url=url
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def geturl(self):return self.url
    def read(self,n):return self.body[:n]


class Opener:
    def __init__(self,value=None,error=None):self.value=value;self.error=error;self.requests=[]
    def open(self,request,timeout):
        self.requests.append(request)
        if self.error:raise self.error
        body=self.value if isinstance(self.value,bytes) else json.dumps(self.value).encode()
        return Response(body,request.full_url)


class ClientTests(unittest.TestCase):
    def stream_opener(self,events):
        class StreamResponse(io.BytesIO):
            def geturl(inner):return 'https://api.deepseek.com/chat/completions'
        class StreamOpener:
            def __init__(inner):inner.requests=[]
            def open(inner,request,timeout):
                inner.requests.append(request)
                return StreamResponse(''.join('data: '+(json.dumps(event,ensure_ascii=False) if isinstance(event,dict) else event)+'\n\n' for event in events).encode())
        return StreamOpener()

    def test_real_sse_deltas_are_decoded_without_exposing_json(self):
        events=[{'id':'stream-fixture','choices':[{'delta':{'content':piece},'finish_reason':None}]} for piece in ['{"summary":"先','稳定','身体。","explanations":[],"next_focus":[],"limitations":[]}']]
        events.extend([{'choices':[{'delta':{},'finish_reason':'stop'}]}, {'choices':[],'usage':{'prompt_tokens':100,'completion_tokens':20}}, '[DONE]'])
        opener=self.stream_opener(events);parts=[]
        value=DeepSeekClient(opener=opener).generate_stream(KEY,'deepseek-flash',self.messages(),256,on_summary=parts.append)
        self.assertEqual(parts,['先','先稳定','先稳定身体。'])
        self.assertEqual(value['result']['summary'],'先稳定身体。');self.assertEqual(value['usage']['completion_tokens'],20)
        payload=json.loads(opener.requests[0].data);self.assertTrue(payload['stream']);self.assertTrue(payload['stream_options']['include_usage'])

    def test_split_json_escapes_do_not_leak_incomplete_characters(self):
        self.assertEqual(streaming_summary('{"summary":"先\\u5b'),'先')
        self.assertEqual(streaming_summary('{"summary":"先\\u5b9a'),'先定')
        self.assertEqual(streaming_summary('{"summary":"说\\"稳\\"'),'说"稳"')
        self.assertEqual(streaming_summary('{"explanations":[],"summary":"隐藏"'), '')

    def test_incomplete_stream_preserves_known_usage_and_never_succeeds(self):
        events=[{'choices':[{'delta':{'content':'{"summary":"未完成"}'},'finish_reason':'length'}]},
                {'choices':[],'usage':{'prompt_tokens':100,'completion_tokens':20}},'[DONE]']
        with self.assertRaises(ProviderError) as got:DeepSeekClient(opener=self.stream_opener(events)).generate_stream(KEY,'deepseek-flash',self.messages())
        self.assertEqual(got.exception.code,'AI_OUTPUT_INCOMPLETE');self.assertIsNotNone(got.exception.estimated_micro_cny)
        self.assertFalse(got.exception.uncertain_charge)
        events[0]['choices'][0]['finish_reason']='stop'
        with self.assertRaises(ProviderError):DeepSeekClient(opener=self.stream_opener(events[:-1])).generate_stream(KEY,'deepseek-flash',self.messages())

    def toolset(self):return [{'type':'function','function':{'name':'estimate_nutrition','parameters':{'type':'object'}}}]
    def test_tool_protocol_preserves_real_call_id_and_usage(self):
        raw=self.success();raw['choices'][0]={'finish_reason':'tool_calls','message':{'role':'assistant','content':None,
            'tool_calls':[{'id':'call-1','type':'function','function':{'name':'estimate_nutrition','arguments':'{"input_version":3}'}}]}}
        opener=Opener(raw);r=DeepSeekClient(opener=opener).tool_completion(KEY,'deepseek-flash',self.messages(),self.toolset())
        self.assertEqual(r['calls'][0]['arguments'],{'input_version':3})
        self.assertEqual(r['message']['tool_calls'][0]['id'],'call-1')
        payload=json.loads(opener.requests[0].data);self.assertEqual(payload['tool_choice'],'auto')
        self.assertNotIn('response_format',payload);self.assertEqual(r['estimated_micro_cny'],262)
    def test_tool_rejection_keeps_known_bill_and_does_not_execute(self):
        for arguments,name,reason in [('[]','estimate_nutrition','tool_calls'),('{"x":NaN}','estimate_nutrition','tool_calls'),
                                     ('{}','arbitrary_shell','tool_calls'),('{}','estimate_nutrition','length')]:
            raw=self.success();raw['choices'][0]={'finish_reason':reason,'message':{'content':None,'tool_calls':[
                {'id':'call-1','type':'function','function':{'name':name,'arguments':arguments}}]}}
            opener=Opener(raw)
            with self.assertRaises(ProviderError) as got:DeepSeekClient(opener=opener).tool_completion(KEY,'deepseek-flash',self.messages(),self.toolset())
            self.assertIsNotNone(got.exception.estimated_micro_cny);self.assertFalse(got.exception.uncertain_charge)
            self.assertEqual(len(opener.requests),1)
    def test_duplicate_call_and_disallowed_last_turn_are_rejected(self):
        call={'id':'same','type':'function','function':{'name':'estimate_nutrition','arguments':'{}'}}
        for calls,allow in [([call,call],True),([call],False)]:
            raw=self.success();raw['choices'][0]={'finish_reason':'tool_calls','message':{'content':None,'tool_calls':calls}}
            with self.assertRaises(ProviderError):DeepSeekClient(opener=Opener(raw)).tool_completion(KEY,'deepseek-flash',self.messages(),self.toolset(),allow_tools=allow)

    def messages(self):return [{'role':'system','content':'只返回JSON对象'},{'role':'user','content':'依据e1解释本段提示'}]
    def success(self):return {'id':'protocol-fixture','choices':[{'finish_reason':'stop','message':{'content':'{"summary":"协议测试"}'}}],
                              'usage':{'prompt_tokens':100,'completion_tokens':20,'prompt_cache_hit_tokens':50}}

    def test_authentication_json_mode_and_usage_are_validated(self):
        opener=Opener(self.success());client=DeepSeekClient(opener=opener)
        r=client.generate(KEY,'deepseek-flash',self.messages(),256)
        self.assertEqual(r['usage']['prompt_tokens'],100);self.assertEqual(r['estimated_micro_cny'],262)
        request=opener.requests[0];self.assertEqual(request.full_url,'https://api.deepseek.com/chat/completions')
        self.assertEqual(request.get_header('Authorization'),'Bearer '+KEY)
        payload=json.loads(request.data);self.assertEqual(payload['thinking'],{'type':'disabled'})
        self.assertEqual(payload['response_format'],{'type':'json_object'});self.assertFalse(payload['stream'])
        self.assertGreaterEqual(estimate_reservation('deepseek-flash',self.messages(),256),r['estimated_micro_cny'])

    def test_provider_errors_do_not_echo_body_or_retry(self):
        for status in (401,402,429,500):
            with self.subTest(status=status):
                error=urllib.error.HTTPError('https://api.deepseek.com/chat/completions',status,'upstream',{},io.BytesIO(KEY.encode()))
                opener=Opener(error=error)
                with self.assertRaises(ProviderError) as got:DeepSeekClient(opener=opener).generate(KEY,'deepseek-flash',self.messages())
                self.assertNotIn(KEY,str(got.exception));self.assertEqual(len(opener.requests),1)
                self.assertEqual(got.exception.uncertain_charge,status>=500)
        opener=Opener(error=TimeoutError())
        with self.assertRaises(ProviderError) as got:DeepSeekClient(opener=opener).generate(KEY,'deepseek-flash',self.messages())
        self.assertTrue(got.exception.uncertain_charge)

    def test_empty_truncated_bad_json_and_invalid_usage_are_not_success(self):
        for kind in ('empty','truncated','bad_json','bad_usage'):
            data=self.success()
            if kind=='empty':data['choices'][0]['message']['content']=''
            if kind=='truncated':data['choices'][0]['finish_reason']='length'
            if kind=='bad_json':data['choices'][0]['message']['content']='not json'
            if kind=='bad_usage':data['usage']['prompt_tokens']=True
            with self.subTest(kind=kind),self.assertRaises(ProviderError):
                DeepSeekClient(opener=Opener(data)).generate(KEY,'deepseek-flash',self.messages())

    def test_connection_test_is_not_a_generation_and_redirects_are_refused(self):
        opener=Opener({'data':[{'id':'deepseek-flash'},{'id':'unrecognized'}]})
        r=DeepSeekClient(opener=opener).test(KEY)
        self.assertTrue(r['connection_verified']);self.assertFalse(r['generation_verified'])
        self.assertEqual(r['available_models'],['deepseek-flash']);self.assertEqual(opener.requests[0].get_method(),'GET')
        self.assertIsNone(NoRedirect().redirect_request(None,None,302,None,None,'https://other.invalid'))

    def test_known_usage_is_kept_for_failed_content_and_unknown_fields_are_dropped(self):
        data=self.success();data['usage']['unexpected_secret']=KEY;data['id']='echo:'+KEY
        result=DeepSeekClient(opener=Opener(data)).generate(KEY,'deepseek-flash',self.messages())
        self.assertNotIn(KEY,json.dumps(result));self.assertEqual(result['usage']['total_tokens'],120)
        data['choices'][0]['finish_reason']='length'
        with self.assertRaises(ProviderError) as got:DeepSeekClient(opener=Opener(data)).generate(KEY,'deepseek-flash',self.messages())
        self.assertEqual(got.exception.usage['prompt_tokens'],100);self.assertEqual(got.exception.estimated_micro_cny,262)
        self.assertFalse(got.exception.uncertain_charge)


if __name__=='__main__':unittest.main()
