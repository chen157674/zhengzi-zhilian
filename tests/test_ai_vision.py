"""视觉请求、授权、预算与结果校验；使用图片夹具及供应商替身，不访问收费API。"""
import base64
import json
import unittest
from io import BytesIO
from unittest.mock import patch

from PIL import Image

from fitness_app.ai_context import validate_fitness_question,validate_answer
from fitness_app.ai_vision import validate_frames, attach_frames, input_budget
from fitness_app.deepseek_client import DeepSeekClient, estimate_reservation
from fitness_app import db_schema as s
import test_ai_jobs as job_fixtures
import test_deepseek_client as protocol_fixtures


def picture(size=(96, 64), stamp=1200, noise=False):
    memory = BytesIO()
    image = Image.effect_noise(size, 80).convert('RGB') if noise else Image.new('RGB', size, (80, 120, 160))
    image.save(memory, format='JPEG')
    return {'data_url': 'data:image/jpeg;base64,' + base64.b64encode(memory.getvalue()).decode(), 'source_ms': stamp}


class VisionInputTests(unittest.TestCase):
    def test_negated_perfection_limitations_pass_but_following_positive_claims_do_not(self):
        bundle={'visual_form_review':True,'read_scope':{},'facts':{'visual_input':{'frame_count':6},
            'evidence':[],'knowledge':[],'selected_records':[],'history':None,'comparison':None,'live':None}}
        raw={'summary':'画面中腰背轮廓比较平直，不能判定全程背部完全标准。',
            'explanations':[],'next_focus':[],'limitations':['无法确认整个动作已经完全标准。']}
        result=validate_answer(raw,bundle)
        self.assertEqual(result['summary'],raw['summary'])
        self.assertIn('无法确认整个动作已经完全标准。',result['limitations'])
        for summary in ('背部完全标准。','动作完美。',
            '不能判定全程背部完全标准，但动作完全标准。',
            '不代表动作完全标准，不过保证安全。',
            '无法确认整个动作完全标准。现在可以增加负重。',
            '不能判定全程背部完全标准，躯干倾斜20度。',
            '不能判定你之前有问题所以动作完全标准。'):
            with self.subTest(summary=summary),self.assertRaises(ValueError):
                validate_answer({**raw,'summary':summary},bundle)

    def test_direct_form_followup_and_negative_load_advice_are_accepted(self):
        from fitness_app.ai_context import VISUAL_FORM_QUESTION
        for question in ('动作错误了嘛','动作有问题吗','姿势不对吗'):
            validate_fitness_question(question);self.assertTrue(VISUAL_FORM_QUESTION.search(question))
        bundle={'visual_form_review':True,'read_scope':{},'facts':{'visual_input':{'frame_count':1},
            'evidence':[],'knowledge':[],'selected_records':[],'history':None,'comparison':None,'live':None}}
        raw={'summary':'画面中躯干向一侧偏斜，先坐正，不要直接增加负重。','explanations':[],'next_focus':[],'limitations':[]}
        self.assertEqual(validate_answer(raw,bundle)['summary'],raw['summary'])
        with self.assertRaises(ValueError):validate_answer({**raw,'summary':'不要增加负重，但可以提高负重。'},bundle)

    def test_optional_sampling_interval_does_not_block_an_otherwise_valid_scene_answer(self):
        bundle={'visual_scene_only':True,'read_scope':{},'facts':{'visual_input':{'frame_count':3},
            'evidence':[],'knowledge':[],'selected_records':[],'history':None,'comparison':None,'live':None}}
        raw={'summary':'他正双手握住杠铃。三张画面间隔约0.4至0.5秒，可见上身前倾，手臂向躯干拉动。',
             'explanations':[],'next_focus':[],'limitations':[]}
        result=validate_answer(raw,bundle)
        self.assertNotIn('0.4',result['summary'])
        self.assertIn('手臂向躯干拉动',result['summary'])
        self.assertIn('sampling_interval_clause_omitted',result['display_normalizations'])
        for summary in ['做了3次动作。','躯干倾斜20度。','三张画面间隔0.5秒，保证安全。']:
            with self.subTest(summary=summary),self.assertRaises(ValueError):validate_answer({**raw,'summary':summary},bundle)

    def test_scene_and_capability_questions_are_not_rejected(self):
        for question in ('你有什么用？', '你能做什么', '视频里面的人正在做什么？', '画面里的人在干嘛', '他在做什么？',
                         '这个视频人物有问题嘛', '这个视频哪里不对？','画面里是不是身体倾斜？','检查这段视频','我的动作对吗？','我的姿势有没有问题？'):
            with self.subTest(question=question):
                validate_fitness_question(question)

    def test_inline_images_are_bounded_and_duplicates_removed(self):
        first = picture()
        frames = validate_frames([first, {**first, 'source_ms': 1600}])
        self.assertEqual(len(frames), 1)
        self.assertEqual(len(frames[0]['sha256']), 64)
        for invalid in ([], [picture((641, 1))], [{'data_url': 'https://example.com/a.jpg', 'source_ms': 1}], [picture()] * 7):
            with self.subTest(invalid_type=type(invalid)), self.assertRaises(ValueError):
                validate_frames(invalid)

    def test_image_tokens_are_budgeted_without_counting_base64_as_text(self):
        plain = [{'role': 'user', 'content': 'describe'}]
        messages = [dict(plain[0])]
        attach_frames(messages, validate_frames([picture()]))
        tokens, count = input_budget(messages)
        self.assertEqual(count, 1)
        self.assertLess(tokens, 1200)
        self.assertGreater(estimate_reservation('deepseek-flash', messages, 128), estimate_reservation('deepseek-flash', plain, 128))
        with self.assertRaises(ValueError):
            input_budget([{'role': 'system', 'content': messages[0]['content']}])

    def test_transport_preserves_multimodal_content(self):
        messages = [{'role': 'user', 'content': 'describe'}]
        attach_frames(messages, validate_frames([picture()]))
        client = DeepSeekClient()
        response = {'choices': [{'message': {'content': json.dumps({'summary': '人物正坐着。', 'explanations': [], 'next_focus': [], 'limitations': []})}, 'finish_reason': 'stop'}], 'usage': {'prompt_tokens': 100, 'completion_tokens': 40}, 'id': 'test'}
        with patch.object(client, '_request', return_value=response) as request:
            answer = client.generate('sk-fixture-vision-123456', 'deepseek-flash', messages)
        self.assertEqual(answer['result']['summary'], '人物正坐着。')
        self.assertEqual(request.call_args.args[2]['messages'][-1]['content'][-1]['type'], 'image_url')
        with self.assertRaises(ValueError):
            client.generate('sk-fixture-vision-123456', 'deepseek-v4-pro', messages)

    def test_stream_transport_accepts_images_and_keeps_usage_accounting(self):
        messages = [{'role': 'user', 'content': 'describe'}]
        attach_frames(messages, validate_frames([picture()]))
        events = [
            {'choices': [{'delta': {'content': '{"summary":"人物正坐着。","explanations":[],"next_focus":[],"limitations":[]}'}, 'finish_reason': None}]},
            {'choices': [{'delta': {}, 'finish_reason': 'stop'}]},
            {'choices': [], 'usage': {'prompt_tokens': 700, 'completion_tokens': 40}}, '[DONE]']
        opener = protocol_fixtures.ClientTests().stream_opener(events)
        answer = DeepSeekClient(opener=opener).generate_stream('sk-fixture-vision-123456', 'deepseek-flash', messages)
        self.assertEqual(answer['result']['summary'], '人物正坐着。')
        self.assertEqual(answer['usage']['prompt_tokens'], 700)
        payload = json.loads(opener.requests[0].data)
        self.assertEqual(payload['messages'][-1]['content'][-1]['image_url']['detail'], 'original')


class VisionChatTests(unittest.TestCase):
    def setUp(self):
        self.fixture = job_fixtures.AIJobsTests()
        self.fixture.setUp()
        self.fx, self.manager = self.fixture.fx, self.fixture.manager
        self.calls = 0
        self.image_count = 0

        class Provider:
            def generate(inner, key, model, messages, max_output_tokens):
                self.calls += 1
                content = messages[-1]['content']
                self.image_count = sum(part['type'] == 'image_url' for part in content) if isinstance(content, list) else 0
                return {'result': {'summary': '前面的人坐着面对镜头，后面的人坐在电脑旁。', 'explanations': [], 'next_focus': [], 'limitations': []}, 'usage': {'prompt_tokens': 100, 'completion_tokens': 40}, 'estimated_micro_cny': 520}

        self.fx.app.extensions['ai_provider_factory'] = Provider

    def tearDown(self):
        self.fixture.tearDown()

    def send(self, **overrides):
        conversation = self.fixture.conversation()
        data = {'text': '视频里面的人在做什么？', 'consent': True, 'expected_context_revision': conversation['context_revision'], **overrides}
        return self.fixture.post('/api/v1/ai/conversations/' + conversation['conversation_id'] + '/messages', data)

    def test_image_question_reaches_provider_and_original_images_are_not_persisted(self):
        frame = picture((640, 480), noise=True)
        self.assertGreater(len(frame['data_url']), 16384)
        response = self.send(include_current_frame=True, frames=[frame])
        self.assertEqual(response.status_code, 202, response.json)
        rid = response.json['data']['request_id']
        result = self.fixture.wait(rid)
        self.assertEqual(result['status'], 'succeeded', result)
        self.assertEqual(self.image_count, 1)
        self.assertIn('坐着', result['result']['summary'])
        with self.fx.db.transaction(False) as connection:
            rows = connection.execute(s.ai_requests.select()).mappings().all()
        self.assertNotIn('data:image', json.dumps([dict(row) for row in rows]))
        self.assertNotIn('data:image', json.dumps(self.manager.conversations))

    def test_no_image_is_sent_without_its_own_consent(self):
        response = self.send(frames=[picture()])
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json['error']['code'], 'AI_IMAGE_CONSENT_REQUIRED')
        self.assertEqual(self.calls, 0)

    def test_form_question_reviews_the_images_instead_of_rejecting_or_description_only(self):
        captured=[]
        class Reviewer:
            def generate(inner,key,model,messages,max_output_tokens):
                captured.extend(messages)
                return {'result':{'summary':'画面中躯干向一侧偏斜，建议先坐正并稳定骨盆后再下拉。',
                    'explanations':[],'next_focus':[],'limitations':[]},
                    'usage':{'prompt_tokens':300,'completion_tokens':60},'estimated_micro_cny':1080}
        self.fx.app.extensions['ai_provider_factory']=Reviewer
        response=self.send(text='这个视频人物有问题嘛',include_current_frame=True,frames=[picture()])
        self.assertEqual(response.status_code,202,response.json)
        result=self.fixture.wait(response.json['data']['request_id'])
        self.assertEqual(result['status'],'succeeded',result)
        self.assertIn('躯干向一侧偏斜',result['result']['summary'])
        self.assertEqual(result['result']['response_basis'],'open_conversation')
        self.assertIn('有图片时直接观察本次实际画面',captured[0]['content'])
        self.assertNotIn('本次 visual_scene_only=true',captured[0]['content'])
        self.assertEqual(captured[-1]['content'][-1]['type'],'image_url')
        with self.fx.db.transaction(False) as connection:
            rows=connection.execute(s.ai_requests.select()).mappings().all()
        self.assertNotIn('data:image',json.dumps([dict(row) for row in rows]))

    def test_structured_visual_problem_survives_real_job_and_keeps_sample_source(self):
        from test_ai_visual_review import answer
        class StructuredProvider:
            def generate(inner,key,model,messages,max_output_tokens):
                raw=answer();raw['visual_findings'][0]['frame_indices']=[1]
                return {'result':raw,'usage':{'prompt_tokens':300,'completion_tokens':60},'estimated_micro_cny':1080}
        self.fx.app.extensions['ai_provider_factory']=StructuredProvider
        response=self.send(text='这个视频人物有问题嘛',include_current_frame=True,frames=[picture(stamp=2468)])
        self.assertEqual(response.status_code,202,response.json)
        result=self.fixture.wait(response.json['data']['request_id'])
        self.assertEqual(result['status'],'succeeded',result)
        self.assertNotIn('visual_review',result['result'])
        item=result['result']['facts']['visual_input']['frames'][0]
        self.assertEqual(item['source_ms'],2468)
        self.assertEqual(result['result']['response_basis'],'open_conversation')
        with self.fx.db.transaction(False) as connection:
            rows=connection.execute(s.ai_requests.select()).mappings().all()
        self.assertNotIn('data:image',json.dumps([dict(row) for row in rows]))

    def test_saved_issue_can_be_explained_without_claiming_to_have_the_original_picture(self):
        captured=[]
        class SummaryProvider:
            def generate(inner,key,model,messages,max_output_tokens):
                captured.extend(messages)
                return {'result':{'summary':'根据已保存的检测结果，本次有需要核对的姿态提示。',
                    'explanations':[],'next_focus':[],'limitations':[]},
                    'usage':{'prompt_tokens':100,'completion_tokens':30},'estimated_micro_cny':440}
        self.fx.app.extensions['ai_provider_factory']=SummaryProvider
        response=self.send(text='这个视频人物有问题嘛')
        self.assertEqual(response.status_code,202,response.json)
        result=self.fixture.wait(response.json['data']['request_id'])
        self.assertEqual(result['status'],'succeeded',result)
        self.assertIsInstance(captured[-1]['content'],str)
        self.assertNotIn('visual_input',result['result']['facts'])
        self.assertIn('已保存',result['result']['summary'])

    def test_related_old_issues_do_not_substitute_for_the_selected_video(self):
        sid=self.fixture.fixture.create([])
        response=self.fixture.post('/api/v1/ai/conversations',{'context_kind':'record','context_id':sid,'history_scope':'all'})
        conversation=response.json['data']
        response=self.fixture.post('/api/v1/ai/conversations/'+conversation['conversation_id']+'/messages',{
            'text':'这个视频人物有问题嘛','consent':True,'expected_context_revision':conversation['context_revision']})
        self.assertEqual(response.status_code,202,response.json)
        result=self.fixture.wait(response.json['data']['request_id'])
        self.assertEqual(result['status'],'succeeded',result)
        self.assertNotIn('visual_input',result['result']['facts'])
    def test_missing_picture_explains_how_to_attach_without_a_paid_call(self):
        response = self.send()
        self.assertEqual(response.status_code,202,response.json)
        result=self.fixture.wait(response.json['data']['request_id'])
        self.assertEqual(result['status'],'succeeded',result)
        self.assertNotIn('visual_input',result['result']['facts'])
        self.assertEqual(self.calls,1)

    def test_picture_question_uses_current_live_snapshot_after_action_change(self):
        snap = {'status': 'active', 'result': {'frame_seq': 1, 'captured_ms': 1000,
            'recognition': {'action_id': None, 'state': 'searching'}, 'automatic_analysis': {'segment_id': None}}}
        with patch.object(self.fx.app.extensions['preview_manager'], 'snapshot', return_value=snap):
            response = self.fixture.post('/api/v1/ai/conversations', {'context_kind': 'live', 'context_id': s.new_id(), 'history_scope': 'none'})
            self.assertEqual(response.status_code, 201, response.json)
            conversation = response.json['data']
            snap['result'].update(frame_seq=8, captured_ms=3500)
            snap['result']['recognition']['action_id'] = 'barbell_overhead_press'
            snap['result']['automatic_analysis'].update(action_id='barbell_overhead_press', segment_id=s.new_id())
            response = self.fixture.post('/api/v1/ai/conversations/' + conversation['conversation_id'] + '/messages', {
                'text': '视频里的人在做什么？', 'consent': True, 'include_current_frame': True, 'frames': [picture()],
                'expected_context_revision': conversation['context_revision']})
            self.assertEqual(response.status_code, 202, response.json)
            result = self.fixture.wait(response.json['data']['request_id'])
            self.assertEqual(result['status'], 'succeeded', result)
            self.assertEqual(result['result']['facts']['live']['frame_seq'], 8)
            snap.update(status='released', result=None)
            frozen=self.fx.client.get('/api/v1/ai/requests/'+response.json['data']['request_id']).json['data']
            self.assertTrue(frozen['context_current'])
            self.assertIn('坐着', frozen['result']['summary'])

    def test_bad_images_and_unsupported_model_never_reach_provider(self):
        response = self.send(include_current_frame=True, frames=[{'data_url': 'not-a-picture', 'source_ms': 1}])
        self.assertEqual(response.status_code, 422)
        self.fx.app.extensions['ai_settings'].write(self.fx.user, self.fx.auth, {'expected_version': 1, 'model': 'deepseek-v4-pro'}, s.new_id())
        self.assertFalse(self.fixture.conversation()['image_input_supported'])
        response = self.send(include_current_frame=True, frames=[picture()])
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json['error']['code'], 'AI_VISION_MODEL_UNAVAILABLE')
        self.assertEqual(self.calls, 0)

