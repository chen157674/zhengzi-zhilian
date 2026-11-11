"""肩推检测部位/方向从保存摘要到AI输出；合成证据不充当实际纠错真值。"""
import copy,json,unittest
from unittest.mock import patch
from fitness_app.ai_context import AIContextBuilder,validate_answer,PROMPT_VERSION
from fitness_app.training_insights import ai_observation_evidence,record_evidence
from fitness_app.posture_rules import rules_for


def evidence():
    return {'affected_sides':['right'],'arm_deviation':{'right':{'ratio':.5,'direction':'outward'}},
        'scope':'起始位可见肘腕关系；不验证全身站姿','observed_duration_ms':900,'value_min':.4,'value_max':.5,'private_token':'never-send'}


def bundle():
    rule=rules_for('barbell_overhead_press')[0]
    observed={**rule,'id':'e:press','action_id':'barbell_overhead_press','status':'issue','side':'none','evidence':ai_observation_evidence(evidence())}
    return {'facts':{'evidence':[observed],'knowledge':[],'selected_records':[],'history':None,'comparison':None,'live':None},'read_scope':{}}


def answer():
    return {'summary':'本次起始位右肘相对手腕偏外。','explanations':[{'evidence_id':'e:press','explanation':'所引用起始位的右肘相对手腕偏外。','adjustment':'尝试将右肘向内轻轻收回。'}],
        'next_focus':[{'text':'下次先核对右肘与手腕的对齐。','evidence_ids':['e:press']}],'limitations':['这只是起始位的局部参考。']}


class AnatomicalGroundingTests(unittest.TestCase):
    def test_saved_reference_keeps_sides_directions_and_scope_without_nested_extras(self):
        raw=evidence();raw['arm_deviation']['right']['private_token']='never-send';raw['arm_deviation']['other']={'direction':'outward','ratio':.5}
        rule=rules_for('barbell_overhead_press')[0]
        data={'session_id':'record','action_id':'barbell_overhead_press','supported_items':[rule],
            'posture':{'findings':[{**rule,'status':'issue','side':'none','evidence':raw}]}}
        before=copy.deepcopy(raw);observed=record_evidence(data)[0]['evidence']
        self.assertEqual(observed['affected_sides'],['right']);self.assertEqual(observed['arm_deviation'],{'right':{'direction':'outward','ratio':.5}})
        self.assertIn('起始位',observed['scope']);self.assertEqual(observed['observed_duration_ms'],900)
        self.assertNotIn('never-send',json.dumps(observed));self.assertEqual(raw,before)

    def test_invalid_nested_direction_or_nonfinite_values_are_not_sent(self):
        for change in ({'ratio':-.5,'direction':'outward'},{'ratio':float('nan'),'direction':'outward'},{'ratio':True,'direction':'outward'}):
            observed=ai_observation_evidence({**evidence(),'arm_deviation':{'right':change},'scope':{},'value_min':float('inf'),'observed_duration_ms':-1})
            self.assertEqual(observed['arm_deviation'],{});self.assertNotIn('scope',observed);self.assertNotIn('value_min',observed);self.assertNotIn('observed_duration_ms',observed)

    def test_correct_observation_and_opposite_corrective_motion_can_coexist(self):
        result=validate_answer(answer(),bundle())
        self.assertEqual(result['prompt_version'],PROMPT_VERSION)
        self.assertEqual(result['explanations'][0]['adjustment'],bundle()['facts']['evidence'][0]['suggestion'])
        self.assertIn('右肘',result['summary'])

    def test_wrong_side_or_observation_direction_rejected_in_all_three_text_surfaces(self):
        for surface in ('summary','explanation','next_focus'):
            for wrong in ('左肘相对手腕偏外。','右肘相对手腕偏内。'):
                with self.subTest(surface=surface,wrong=wrong):
                    value=answer()
                    if surface=='summary':value['summary']=wrong
                    elif surface=='explanation':value['explanations'][0]['explanation']=wrong
                    else:value['next_focus'][0]['text']=wrong
                    with self.assertRaises(ValueError):validate_answer(value,bundle())

    def test_bilateral_different_directions_do_not_get_merged_or_flipped(self):
        context=bundle();observed=context['facts']['evidence'][0]
        observed['evidence']['affected_sides']=['left','right'];observed['evidence']['arm_deviation']['left']={'ratio':-.5,'direction':'inward'}
        value=answer();value['summary']='起始位左肘偏内，右肘偏外。';value['explanations'][0]['explanation']=value['summary']
        self.assertEqual(validate_answer(value,context)['summary'],value['summary'])
        value['explanations'][0]['explanation']='起始位左肘偏外，右肘偏外。'
        with self.assertRaises(ValueError):validate_answer(value,context)

    def test_absent_direction_or_nonissue_cannot_become_directional_error(self):
        for change in ('missing','unassessable','not_observed'):
            context=bundle()
            if change=='missing':context['facts']['evidence'][0]['evidence'].pop('arm_deviation')
            else:context['facts']['evidence'][0]['status']=change
            with self.assertRaises(ValueError):validate_answer(answer(),context)

    def test_summary_without_details_and_focus_without_reference_still_cannot_invent_side(self):
        value=answer();value['explanations']=[];value['next_focus']=[];value['summary']='先调整左肘。'
        with self.assertRaises(ValueError):validate_answer(value,bundle())

    def test_uncertainty_statement_is_not_mistaken_for_a_directional_error_claim(self):
        context=bundle();context['facts']['evidence'][0]['status']='unassessable';context['facts']['evidence'][0]['evidence'].pop('arm_deviation')
        value=answer();value['summary']='当前无法判断右肘是否偏外。';value['explanations'][0]['explanation']='当前无法判断右肘是否偏外。'
        result=validate_answer(value,context);self.assertEqual(result['explanations'][0]['adjustment_source'],'assessment_coverage')

    def test_camera_side_instruction_is_not_an_anatomical_side_assertion(self):
        value=answer();value['summary']='请从左侧拍摄，再核对当前提示。';value['explanations'][0]['explanation']='可以从左侧拍摄相关部位。'
        value['next_focus']=[{'text':'可从左侧拍摄。','evidence_ids':[]}]
        result=validate_answer(value,bundle());self.assertIn('拍摄',result['summary'])
        value=answer();value['next_focus'][0]['evidence_ids']=[]
        with self.assertRaises(ValueError):validate_answer(value,bundle())

    def test_live_builder_retains_same_direction_scope_and_old_prompt_is_stale(self):
        from test_ai_context import ContextTests,live_snapshot_fixture
        fixture=ContextTests();fixture.setUp()
        try:
            pid,sid,snap=live_snapshot_fixture(fixture.fx);cue=snap['result']['coaching']['primary_cue'];cue.update(side='none',evidence=evidence())
            with patch.object(fixture.fx.app.extensions['preview_manager'],'snapshot',return_value=snap):
                context=fixture.builder.build(fixture.fx.user,fixture.fx.auth,{'context_kind':'live','context_id':pid,'text':'怎么调整？','history_scope':'none'})
                observed=context['facts']['evidence'][0]['evidence']
                self.assertEqual(observed['affected_sides'],['right']);self.assertEqual(observed['arm_deviation']['right']['direction'],'outward')
                self.assertIn('起始位',observed['scope']);self.assertNotIn('never-send',json.dumps(context))
                old={**context['reference'],'prompt_version':'grounded-coaching-8'}
                self.assertFalse(fixture.builder.current(fixture.fx.user,fixture.fx.auth,old))
        finally:fixture.tearDown()

    def test_http_wrong_direction_is_failed_with_usage_retained_and_same_id_not_resent(self):
        from test_ai_jobs import AIJobsTests
        from test_ai_context import live_snapshot_fixture
        fixture=AIJobsTests();fixture.setUp()
        try:
            pid,sid,snap=live_snapshot_fixture(fixture.fx);snap['result']['coaching']['primary_cue'].update(side='none',evidence=evidence())
            def generate(key,model,messages,max_output_tokens):
                fixture.provider.calls+=1;facts=json.loads(messages[-1]['content'])['facts'];observed=facts['evidence'][0]
                self.assertEqual(observed['evidence']['arm_deviation']['right']['direction'],'outward')
                self.assertNotIn('never-send',json.dumps(facts));result=answer();result['summary']='起始位右肘偏内。'
                result['explanations'][0]['evidence_id']=observed['id'];result['next_focus'][0]['evidence_ids']=[observed['id']]
                return {'result':result,'usage':{'prompt_tokens':30,'completion_tokens':10},'estimated_micro_cny':140}
            with patch.object(fixture.fx.app.extensions['preview_manager'],'snapshot',return_value=snap),patch.object(fixture.provider,'generate',side_effect=generate):
                # 结构化报告走证据校验；开放聊天使用独立的自然语言回复路径。
                payload={'kind':'interpretation','context_kind':'live','context_id':pid,'history_scope':'none',
                    'text':'应该怎样调整？','consent':True}
                sent=fixture.post('/api/v1/ai/reports',payload)
                self.assertEqual(sent.status_code,202,sent.json);rid=sent.json['data']['request_id'];row=fixture.wait(rid)
                self.assertEqual(row['status'],'failed');self.assertEqual(row['error_code'],'AI_EVIDENCE_INVALID');self.assertIsNone(row['result']);self.assertEqual(row['estimated_micro_cny'],140)
                fixture.post('/api/v1/ai/reports',payload,rid);self.assertEqual(fixture.provider.calls,1)
        finally:fixture.tearDown()


if __name__=='__main__':unittest.main()
