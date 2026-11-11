"""一次真实开放聊天验证；只发普通文字及合成图，不外传私人视频。"""
import argparse,json,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment
configure_environment()
sys.path.insert(0,str(ROOT/'tests'))
from scripts.verify_ai_visual_provider import system_key,diagram
from fitness_app.deepseek_client import DeepSeekClient
from fitness_app.ai_context import PROMPT_VERSION
from fitness_app.research_extract import atomic_json
from fitness_app import db_schema as s
import test_ai_jobs as fixtures
REPORT=ROOT/'data/verification/round3/open-ai-chat-provider-review.json'


def run(database):
    if REPORT.exists():raise ValueError('该开放聊天真实验证已尝试，不自动重发')
    key=system_key(database);fixture=fixtures.AIJobsTests();fixture.setUp();fx=fixture.fx
    doc={'state':'running','pid':__import__('os').getpid(),'prompt_version':PROMPT_VERSION,'private_media_uploaded':False,
        'production_database_modified':False,'scope':'real_open_chat_protocol_synthetic_image_not_accuracy','cases':[]}
    atomic_json(REPORT,doc)
    try:
        fx.app.extensions['ai_settings'].write(fx.user,fx.auth,{'expected_version':1,'personal_key':key},s.new_id());key=None
        fx.app.extensions['ai_provider_factory']=DeepSeekClient
        c=fixture.conversation()
        for name,text,frames in [('general_math','请计算2+3，再把“你好”翻译成英文，直接回答。',None),
                ('synthetic_form','我的动作错在哪里，怎么修改？',[diagram(65,1000),diagram(65,1400)])]:
            response=fixture.post('/api/v1/ai/conversations/'+c['conversation_id']+'/messages',
                {'text':text,'consent':True,'expected_context_revision':c['context_revision'],
                    **({'include_current_frame':True,'frames':frames} if frames else {})})
            if response.status_code!=202:raise RuntimeError('真实请求未接纳：'+str(response.json.get('error',{}).get('code')))
            rid=response.json['data']['request_id'];print(json.dumps({'case':name,'request_id':rid,'state':'submitted'}),flush=True)
            while True:
                row=fixture.manager.snapshot(fx.user,fx.auth,rid)
                if row['status'] in {'succeeded','failed','cancelled','uncertain'}:break
                time.sleep(.2)
            case={k:row.get(k) for k in ('status','usage','estimated_micro_cny','error_code')}
            case.update(name=name,request_id=rid,summary=(row.get('result') or {}).get('summary'),response_basis=(row.get('result') or {}).get('response_basis'),input='synthetic_diagram' if frames else 'ordinary_text')
            doc['cases'].append(case);atomic_json(REPORT,doc);print(json.dumps(case,ensure_ascii=False),flush=True)
            if row['status']!='succeeded':raise RuntimeError('真实调用失败，保留原结果，不重试')
        doc['state']='completed'
    except BaseException as error:
        doc.update(state='failed',error={'type':type(error).__name__,'message':str(error)[:500]});raise
    finally:
        directory=fx.db.path.parent;fixture.tearDown();key=None
        doc['test_database_and_key_copy_cleaned']=not directory.exists();doc['calls']=len(doc['cases'])
        doc['estimated_total_micro_cny']=sum(v.get('estimated_micro_cny') or 0 for v in doc['cases']);atomic_json(REPORT,doc)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--database',required=True);args=p.parse_args();run(args.database)
