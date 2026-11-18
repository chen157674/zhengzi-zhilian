"""实际中文PDF的文本、分页与静态安全检查；工程夹具不是用户训练真值。"""
import os,sys,unittest
from io import BytesIO
from pathlib import Path
from fitness_app import db_schema as s
from fitness_app.catalogue import get_action
from fitness_app.posture_rules import rules_for
from fitness_app.export_snapshot import normalize
from fitness_app.pdf_report import render_pdf
import test_formal_records as fixtures


def pdf_reader(data):
    bundled=Path('C:/Users/chen/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/Lib/site-packages')
    sys.path.append(str(bundled))
    try:
        from pypdf import PdfReader
    finally:sys.path.remove(str(bundled))
    return PdfReader(BytesIO(data))


class PdfRenderTests(unittest.TestCase):
    def setUp(self):self.fx=fixtures.RecordTests();self.fx.setUp()
    def tearDown(self):self.fx.app.extensions['report_exports'].stop();self.fx.tearDown()
    def record(self,basis):
        action=get_action('barbell_overhead_press');sid=self.fx.store.create(self.fx.user,self.fx.auth,{'action_id':action['action_id'],'capability_version':action['capability_version'],'source':'camera','mode':'free','target':None,'exercise_basis':basis,'recognition_model_version':'controlled-engineering-fixture'},s.new_id(),stability=True)['session_id'];self.fx.store.set_state(self.fx.user,sid,'running',s.new_id())
        rule=rules_for(action['action_id'])[0];items=[]
        for start,end,status,reason in [(0,1200,'issue',''),(1200,2400,'unassessable','rack_phase_unconfirmed')]:
            items.append({**{k:rule[k] for k in ('code','rule_version','body_parts','suggestion')},'start_ms':start,'end_ms':end,'status':status,'side':'none','message':'起始位右肘与手腕的参考对齐提示' if status=='issue' else '当前未能确认起始位','reason':reason,'evidence_level':'reference','evidence':{'view':'front','view_basis':'user_setting','time_base':'session_elapsed_ms','affected_sides':['right'],'value':.4}})
        self.fx.store.append_posture(self.fx.user,sid,1,items,self.fx.auth,2400);self.fx.finish(sid);return sid
    def snapshot(self):
        first=self.record('user_selected');second=self.record('automatically_recognized');info=self.fx.app.extensions['training_info']
        note=('这段中文用于检查长备注分页和原文换行。'*18)+'\n<script>仅按普通文字显示</script> 攀岩🧗 sk-do-not-export-this-secret-123456789'
        info.write(self.fx.user,self.fx.auth,{'expected_version':0,'load_value':12.5,'load_unit':'kg','load_basis':'per_hand','difficulty':'hard','note':note},s.new_id(),sid=first)
        info.write(self.fx.user,self.fx.auth,{'expected_version':0,'load_value':5,'load_unit':'plate_index','load_basis':'device_setting','difficulty':'moderate'},s.new_id(),sid=second)
        payload=normalize({'record_ids':[first,second],'expected_versions':{sid:self.fx.store.snapshot(self.fx.user,sid)['state_version'] for sid in [first,second]},'include_user_notes':True})
        snapshot,_,_=self.fx.app.extensions['report_exports'].builder.freeze(self.fx.user,self.fx.auth,payload);snapshot['fixture_label']='工程测试报告，非真人检测结果'
        snapshot['ai_reports']=[{'id':s.new_id(),'model':'排版工程测试模型（未调用AI）','kind':'review','created_at_ms':self.fx.now[0],'content':{
            'summary':'以下仅用于检查AI解释区版式，不是实际AI回复。','explanations':[{'evidence_id':'fixture:layout-only','explanation':'演示检测事实与解释分区显示。','adjustment':'本段不提供实际训练建议。'}],
            'next_focus':[{'text':'真实报告须引用实际检测证据。','evidence_ids':['fixture:layout-only']}],'limitations':['本页是工程排版夹具，不是模型质量证明。'],'read_scope':{'selected_records':2,'history_records':0},'prompt_version':'fixture-only'}}]
        return snapshot
    def test_actual_chinese_pdf_pages_text_units_redaction_and_no_active_content(self):
        snapshot=self.snapshot();data=render_pdf(snapshot,s.new_id(),self.fx.now[0]);reader=pdf_reader(data)
        self.assertGreaterEqual(len(reader.pages),4);self.assertLess(len(reader.pages),20);self.assertFalse(reader.is_encrypted)
        text='\n'.join(page.extract_text() for page in reader.pages)
        for expected in ['正姿智练','工程测试报告','用户主动指定或修正','自动识别','配重档位','单手','纠错提示与调整建议','两条记录的可比对照','AI复盘与建议','部位侧别 右']:
            self.assertIn(expected,text)
        self.assertNotIn('sk-do-not-export-this-secret',text);self.assertNotIn('/JavaScript',str(reader.trailer));self.assertNotIn('/Launch',str(reader.trailer))
        self.assertIn('本段分析已结束',text);self.assertNotIn('目标完成前结束',text)
        for page in reader.pages:self.assertIsNone(page.get('/Annots'))
        if os.environ.get('FITNESS_PDF_VERIFY_OUTPUT')=='1':
            target=Path(__file__).resolve().parents[1]/'data/verification/round3/training-report.pdf';target.write_bytes(data);print('Verified PDF sample:',target,'pages:',len(reader.pages),'bytes:',len(data),flush=True)

    def test_normal_export_has_no_internal_fixture_labels(self):
        snapshot=self.snapshot();snapshot.pop('fixture_label');snapshot['ai_reports']=[]
        data=render_pdf(snapshot,s.new_id(),self.fx.now[0])
        text='\n'.join(page.extract_text() for page in pdf_reader(data).pages)
        self.assertIn('训练复盘报告',text)
        for label in ('工程测试','非真人检测','示例报告','工程参考','未经独立','待评估时段','当前未能确认起始位','不替代原检测'):
            self.assertNotIn(label,text)
        self.assertEqual(pdf_reader(data).metadata.title,'正姿智练训练复盘报告')

    def test_target_progress_and_unknown_cycles_are_not_lost(self):
        action=get_action('bodyweight_squat');sid=self.fx.store.create(self.fx.user,self.fx.auth,{'action_id':action['action_id'],'capability_version':action['capability_version'],'source':'camera','mode':'target','target':{'sets':2,'value':3,'unit':'reps','rest_seconds':0}},s.new_id())['session_id'];self.fx.store.set_state(self.fx.user,sid,'running',s.new_id())
        for i in range(1,4):self.fx.store.append_event(self.fx.user,sid,self.fx.event(i))
        self.fx.finish(sid);payload=normalize({'record_ids':[sid],'expected_versions':{sid:self.fx.store.snapshot(self.fx.user,sid)['state_version']}})
        snapshot,_,_=self.fx.app.extensions['report_exports'].builder.freeze(self.fx.user,self.fx.auth,payload);reader=pdf_reader(render_pdf(snapshot,s.new_id(),self.fx.now[0]));text=''.join(p.extract_text() for p in reader.pages)
        for value in ('目标完成前结束','用户预设目标：2 组','已保存完成组数 1','没有可评估判项的周期 3'):self.assertIn(value,text)


if __name__=='__main__':unittest.main()
