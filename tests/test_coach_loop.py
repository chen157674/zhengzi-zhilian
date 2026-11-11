"""回路硬上限、取消、Schema及真实消息链；无供应商网络。"""
import json,unittest
from fitness_app.coach_contracts import CoachLoop,CoachLimit,validate,SPECS

class Provider:
    def __init__(self,batches):self.batches=batches;self.calls=[]
    def tool_completion(self,key,model,messages,tools,max_output_tokens=1200,allow_tools=True):
        self.calls.append((list(messages),allow_tools));batch=self.batches.pop(0)
        calls=[{'id':x,'name':'search_knowledge','arguments':{'query':'深蹲','domain':'movement','action_id':'bodyweight_squat'}} for x in batch]
        msg={'role':'assistant','content':None if calls else '{}'}
        if calls:msg['tool_calls']=[{'id':x['id'],'type':'function','function':{'name':x['name'],'arguments':json.dumps(x['arguments'])}} for x in calls]
        return {'calls':calls,'message':msg,'estimated_micro_cny':360,'usage':{}}

class CoachLoopTests(unittest.TestCase):
    def test_three_turns_max_and_tool_result_ids(self):
        p=Provider([['a'],['b'],[]]);sent=[]
        r=CoachLoop(p,lambda n,a:{'matched':True},lambda n,q:sent.append(q)).run('fixture',[{'role':'user','content':'深蹲'}])
        self.assertEqual((r['model_requests'],r['tool_calls']),(3,2));self.assertEqual(len(sent),3)
        self.assertEqual(p.calls[-1][1],False);self.assertEqual(p.calls[1][0][-1]['tool_call_id'],'a')
    def test_eight_tools_reject_ninth_without_execution(self):
        p=Provider([list('abcdefgh'),['i']]);executed=[]
        with self.assertRaises(CoachLimit):CoachLoop(p,lambda n,a:executed.append(n) or {},lambda n,q:None).run('fixture',[{'role':'user','content':'深蹲'}])
        self.assertEqual(len(executed),8)
    def test_cancel_prevents_next_call(self):
        p=Provider([[]]);r=CoachLoop(p,lambda n,a:{},lambda n,q:None,cancelled=lambda:True).run('fixture',[])
        self.assertEqual(r['status'],'cancelled');self.assertFalse(p.calls)
    def test_schema_rejects_extra_bool_as_int_and_unknown_domain(self):
        for value,name in [({'input_version':True},'estimate_nutrition'),({'query':'x','domain':'medical'},'search_knowledge'),({'query':'x','domain':'nutrition','shell':'x'},'search_knowledge')]:
            with self.assertRaises(ValueError):validate(value,SPECS[name])

if __name__=='__main__':unittest.main()
