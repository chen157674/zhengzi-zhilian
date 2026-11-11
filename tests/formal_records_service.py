"""06 页面故障 fixture；独立测试进程使用，不向正式应用注册故障接口。"""
import json
import os
from pathlib import Path
from sqlalchemy import select,update

from fitness_app import app as app_module
from fitness_app.catalogue import get_action
from fitness_app.auth import initialize_admin,password_hash
from fitness_app.database import StorageError
from fitness_app.paths import ROOT
from fitness_app import db_schema as s
from fitness_app.runtime import serve
from fitness_app.records import SessionStore

temporary=Path(os.environ['FITNESS_DATABASE_PATH']).parent.resolve()
assert temporary==ROOT/'.tmp/tests/ui-step06-check'
control=temporary/'fault.json'
original=app_module.create_app


def seed_accounts():
    bootstrap=original({'TESTING':True,'DATABASE_PATH':os.environ['FITNESS_DATABASE_PATH']})
    db=bootstrap.extensions['database']
    initialize_admin(db,'Record_Admin','record-fixture-password-123')
    with db.transaction() as c:
        c.execute(s.users.insert(),[dict(id=s.new_id(),username_display=name,username_key=name.casefold(),password_hash=password_hash('record-fixture-password-123'))
            for name in ['Record_User','Other_User']])
    db.dispose()


def testing_app(config):
    def fault(point):
        if point=='before_commit' and control.exists() and json.loads(control.read_text())['blocked']:
            raise StorageError('step06 isolated fixture')
    app=original({**config,'TESTING':True,'DATABASE_PATH':os.environ['FITNESS_DATABASE_PATH']})
    db=app.extensions['database']
    # 故障仅覆盖分析保存线程，不把正常历史删除也变成同一故障测试。
    app.extensions['persistence'].store=SessionStore(db,fault=fault)
    with db.transaction(False) as c:uid=c.scalar(select(s.users.c.id).where(s.users.c.username_key=='record_user'))
    def seed():
        client=app.test_client()
        token=client.get('/api/v1/auth/csrf').json['data']['csrf_token']
        result=client.post('/api/v1/auth/login',json={'username':'Record_User','password':'record-fixture-password-123'},headers={'X-CSRFToken':token})
        assert result.status_code==200
        with client.session_transaction() as state:auth=state['_user_id']
        store=app.extensions['session_store'];writer=app.extensions['persistence'];ids=[]
        def create(action):
            result=store.create(uid,auth,dict(action_id=action,capability_version=get_action(action)['capability_version'],source='camera',mode='free',target=None),s.new_id())
            sid=result['session_id'];store.set_state(uid,sid,'running',s.new_id());return sid
        def event(seq):return dict(event_seq=seq,set_index=1,side='none',start_ms=(seq-1)*1000,end_ms=seq*1000,algorithm_version='fixture',feature_schema_version='fixture',assessments=[],active_ms=seq*1000)
        for index in range(23):
            sid=create('barbell_overhead_press' if index==0 else 'bodyweight_squat')
            store.append_event(uid,sid,event(1));store.set_state(uid,sid,'ended_early',s.new_id(),reason='fixture_end')
            with db.transaction() as c:c.execute(update(s.session_sets).where(s.session_sets.c.session_id==sid).values(status='ended_early'))
            ids.append(sid)
        active=create('standard_push_up')
        failed=[]
        for unused in range(2):
            sid=create('bodyweight_squat');writer.submit(uid,sid,auth,'event',event(1));failed.append(sid)
        # 只在全部正常行生成后打开两个待提交故障。
        control.write_text(json.dumps({'blocked':True}),encoding='utf8')
        for sid in failed:writer.submit(uid,sid,auth,'event',event(2))
        (temporary/'fixture.json').write_text(json.dumps({'ids':ids,'active':active,'failed':failed}),encoding='utf8')
    app.extensions['start_hooks'].append(seed)
    return app


seed_accounts()
app_module.create_app=testing_app
serve(0)
