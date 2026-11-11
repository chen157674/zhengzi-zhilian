"""开放聊天完整请求／流式／图像链；保留账号、消费和训练报告隔离。"""
import json,unittest
from unittest.mock import patch
from sqlalchemy import select,func
from fitness_app.ai_context import messages_for,validate_chat_answer,validate_answer
from fitness_app import db_schema as s
from fitness_app.deepseek_client import streaming_summary
import test_ai_jobs as job_fixtures
from test_ai_vision import picture


class OpenChatTests(unittest.TestCase):
    def setUp(self):
        self.fixture=job_fixtures.AIJobsTests();self.fixture.setUp();self.fx=self.fixture.fx;self.manager=self.fixture.manager
    def tearDown(self):self.fixture.tearDown()
    def send(self,text,frames=None,summary=None):
        captured=[]
        class Provider:
            def generate(inner,key,model,messages,max_output_tokens):
                captured.extend(messages)
                return {'result':{'summary':summary or '可以，先看清动作，再放慢回程，尝试每组8–12次。',
                    'explanations':[{'evidence_id':'not-a-real-id','explanation':'额外结构不混入训练记录','adjustment':'额外结构'}]},
                    'usage':{'prompt_tokens':100,'completion_tokens':80},'estimated_micro_cny':840}
        self.fx.app.extensions['ai_provider_factory']=Provider
        conversation=self.fixture.conversation()
        response=self.fixture.post('/api/v1/ai/conversations/'+conversation['conversation_id']+'/messages',
            {'text':text,'expected_context_revision':conversation['context_revision'],'consent':True,
             **({'include_current_frame':True,'frames':frames} if frames else {})})
        self.assertEqual(response.status_code,202,response.json)
        result=self.fixture.wait(response.json['data']['request_id'])
        self.assertEqual(result['status'],'succeeded',result)
        self.assertEqual(result['result']['response_basis'],'open_conversation')
        self.assertEqual(result['result']['explanations'],[])
        return result,captured
    def test_general_math_code_translation_and_personal_nutrition_use_same_chat(self):
        before=self.fx.store.snapshot(self.fx.user,self.fixture.sid,True)
        cases=[('解释勾股定理','直角三角形满足 a²+b²=c²。'),
            ('帮我算2+3','2+3=5。'),('写个Python循环','```python\nfor i in range(3):\n    print(i)\n```'),
            ('把你好翻译为英文','Hello.'),('我70公斤，蛋白质怎么安排','可参考每天约112–154克，再结合饮食习惯调整。')]
        for question,answer in cases:
            with self.subTest(question=question):
                result,messages=self.send(question,summary=answer)
                self.assertEqual(result['result']['summary'],answer)
                self.assertIn('可以回答用户的各种问题',messages[0]['content'])
                self.manager.close(self.fx.user,self.fx.auth,next(reversed(self.manager.conversations)))
        after=self.fx.store.snapshot(self.fx.user,self.fixture.sid,True)
        self.assertEqual(before,after)
        with self.fx.db.transaction(False) as c:self.assertEqual(c.scalar(select(func.count()).select_from(s.ai_requests)),5)
    def test_normal_video_form_question_accepts_numeric_advice_without_local_report_ids(self):
        result,messages=self.send('我的动作错在哪里，怎么修改？',[picture()],
            '画面里肩部有倾斜，先坐正，降低重量，控制回程约2秒。')
        self.assertIn('肩部有倾斜',result['result']['summary'])
        self.assertEqual(messages[-1]['content'][-1]['type'],'image_url')
        self.assertIn('visual_input',result['result']['facts'])
        self.assertEqual(result['estimated_micro_cny'],840)
    def test_no_picture_question_reaches_model_and_can_explain_missing_input(self):
        result,messages=self.send('他现在在做什么？',summary='我还没有收到画面，勾选视频画面后再发给我。')
        self.assertIn('还没有收到画面',result['result']['summary'])
        self.assertIsInstance(messages[-1]['content'],str)
    def test_report_validation_stays_strict_while_chat_preserves_natural_reply(self):
        bundle=self.fixture.manager.builder.build(self.fx.user,self.fx.auth,
            {'context_kind':'record','context_id':self.fixture.sid,'text':'应该怎么练','history_scope':'none'})
        raw={'summary':'可以每组做12次，2+3=5。','explanations':[],'next_focus':[],'limitations':[]}
        with self.assertRaises(ValueError):validate_answer(raw,bundle)
        self.assertEqual(validate_chat_answer(raw,bundle)['summary'],raw['summary'])
        answer='很长的回复。'*180
        self.assertEqual(streaming_summary(json.dumps({'summary':answer},ensure_ascii=False)),answer)
    def test_missing_consent_still_never_calls_provider(self):
        conversation=self.fixture.conversation()
        result=self.fixture.post('/api/v1/ai/conversations/'+conversation['conversation_id']+'/messages',
            {'text':'随便聊聊','expected_context_revision':conversation['context_revision']})
        self.assertEqual(result.status_code,422)
        self.assertEqual(self.fixture.provider.calls,0)


if __name__=='__main__':unittest.main()
