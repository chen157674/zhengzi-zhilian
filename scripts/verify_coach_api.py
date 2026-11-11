"""公开/合成文字的真实工具抽验；预留原系统预算，不复制本人库/密钥文件。"""
import json,os,sqlite3,sys,tempfile,time,hashlib
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment
configure_environment()
from fitness_app import create_app,db_schema as s
from fitness_app.auth import initialize_admin
from fitness_app.ai_secrets import SecretVault
from fitness_app.coach_tasks import SYSTEM

ORIGINAL=ROOT/'.tmp/home-precision-validation/restored-preview/app.sqlite3'
REPORT=ROOT/'data/knowledge/api-verification.json'
CAP=200000

def main():
    # 本轮固定合成输入；无私人训练记录、画面或真实身体资料。
    tasks=[{'label':'synthetic_nutrition','text':'假设一位25岁男性，70公斤，规律运动，整体轻活动。估算每日蛋白质和维持热量。'},
           {'label':'public_movement','text':'一般器械推胸时手腕如何保持，回程要注意什么？请引用知识。'}]
    repeat='--repeat-synthetic' in sys.argv
    if REPORT.exists() and not repeat:raise RuntimeError('已存在抽验记录，需明确指定修复复查参数')
    previous=json.loads(REPORT.read_text(encoding='utf-8')) if REPORT.exists() else {}
    if repeat:tasks=tasks[:1]
    original=sqlite3.connect(ORIGINAL,timeout=2);original.row_factory=sqlite3.Row
    results=list(previous.get('cases',[]))
    for task in tasks:
        original.execute('BEGIN IMMEDIATE')
        policy=original.execute('SELECT * FROM ai_system_policy WHERE id=1').fetchone()
        if not policy or not policy['enabled'] or policy['budget_micro_cny']-policy['spent_micro_cny']-policy['reserved_micro_cny']<CAP:original.rollback();raise RuntimeError('原系统剩余额度不足，未发送')
        if sum(x.get('cost_micro_cny',0) or 0 for x in results)+CAP>1000000:original.rollback();raise RuntimeError('真实验收子额度不足')
        key=SecretVault(ORIGINAL.parent/'private/ai-master.key').open('system',policy['key_ciphertext'])
        original.execute('UPDATE ai_system_policy SET reserved_micro_cny=reserved_micro_cny+? WHERE id=1',(CAP,));original.commit()
        cost=None;uncertain=True
        base=ROOT/'.tmp/tests';base.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base,prefix='coach-paid-') as temporary:
            folder=Path(temporary);os.environ['FITNESS_RUNTIME_DIR']=str(folder/'runtime')
            app=create_app({'TESTING':True,'DATABASE_PATH':str(folder/'app.sqlite3'),'AI_SYSTEM_KEY':key,'COACH_ACCEPTANCE_TEST':True})
            db=app.extensions['database'];jobs=app.extensions['coach_tasks']
            try:
                initialize_admin(db,'Acceptance_Coach','synthetic-acceptance-password-2026')
                client=app.test_client();token=client.get('/api/v1/auth/csrf').json['data']['csrf_token']
                client.post('/api/v1/auth/login',json={'username':'Acceptance_Coach','password':'synthetic-acceptance-password-2026'},headers={'X-CSRFToken':token})
                from sqlalchemy import update,select
                with db.transaction() as c:
                    if c.execute(select(s.ai_system_policy)).first():c.execute(update(s.ai_system_policy).values(enabled=1,budget_micro_cny=CAP,per_user_daily_requests=10))
                    else:c.execute(s.ai_system_policy.insert().values(id=1,enabled=1,budget_micro_cny=CAP,per_user_daily_requests=10))
                token=client.get('/api/v1/auth/csrf').json['data']['csrf_token'];rid=s.new_id()
                response=client.post('/api/v1/coach/tasks',json={'text':task['text'],'consent':True},headers={'X-CSRFToken':token,'Idempotency-Key':rid})
                if response.status_code!=202:raise RuntimeError('创建验收任务未成功：'+response.json['error']['code'])
                for _ in range(180):
                    row=client.get('/api/v1/coach/tasks/'+rid).json['data']
                    if row['status'] not in {'queued','running'}:break
                    time.sleep(.5)
                else:raise RuntimeError('供应商抽验超时，原预留保持待核')
                with db.transaction(False) as c:ledger=dict(c.execute(select(s.ai_requests).where(s.ai_requests.c.id==rid)).mappings().one())
                cost=ledger['estimated_micro_cny'];uncertain=ledger['status']=='uncertain'
                item={'case':task['label'],'task_id':rid,'status':row['status'],'error':row['error'],'model_requests':row['model_requests'],
                      'tool_calls':row['tool_calls'],'cost_micro_cny':cost,'usage':json.loads(ledger['usage_json']) if ledger['usage_json'] else None,
                      'pricing_version':ledger['pricing_version'],'public_or_synthetic_only':True,'tools':row['events']}
                if row['result']:item['result']=row['result']
                item['synthetic_diagnostics']=app.config.get('COACH_TEST_RESPONSES',[])
                results.append(item);print(json.dumps({k:v for k,v in item.items() if k not in {'result'}},ensure_ascii=False))
            finally:
                jobs.stop()
                if jobs.worker:jobs.worker.join(5)
                db.dispose();key=None
                if cost is not None and not uncertain:
                    original.execute('BEGIN IMMEDIATE');original.execute('UPDATE ai_system_policy SET reserved_micro_cny=reserved_micro_cny-?,spent_micro_cny=spent_micro_cny+? WHERE id=1 AND reserved_micro_cny>=?',(CAP,cost,CAP));original.commit()
                else:print('费用状态未知，原系统20分预留未释放，请核对。')
                REPORT.write_text(json.dumps({'cases':results,'total_known_micro_cny':sum(x.get('cost_micro_cny',0) or 0 for x in results),'unknown_reservations_micro_cny':CAP if cost is None or uncertain else 0,'limit_micro_cny':1000000,'materials':'public/synthetic text only'},ensure_ascii=False,indent=2),encoding='utf-8')
        if uncertain or cost is None:break
    original.close()
if __name__=='__main__':main()
