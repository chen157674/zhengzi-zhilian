"""08 隔离 UI 夹具：真实 Full 模型、姿态观察模式和可控状态写入故障。"""
import json
import os
from pathlib import Path
from fitness_app import app as app_module
from fitness_app.auth import initialize_admin,password_hash
from fitness_app.database import StorageError
from fitness_app.paths import ROOT
from fitness_app.runtime import serve
from fitness_app import db_schema as s

temporary=Path(os.environ['FITNESS_DATABASE_PATH']).parent.resolve()
assert temporary==ROOT/'.tmp/tests/ui-step08'
original=app_module.create_app
bootstrap=original({'TESTING':True,'DATABASE_PATH':os.environ['FITNESS_DATABASE_PATH']})
db=bootstrap.extensions['database'];initialize_admin(db,'Flow_Admin','flow-fixture-password-123')
with db.transaction() as c:c.execute(s.users.insert().values(username_display='Flow_User',username_key='flow_user',password_hash=password_hash('flow-fixture-password-123')))
db.dispose()


def testing_app(config):
    def fault(point):
        control=temporary/'fault.json'
        if point=='before_commit' and control.exists() and json.loads(control.read_text())['blocked']:raise StorageError('step08 isolated state write fault')
    return original({**config,'TESTING':True,'DATABASE_PATH':os.environ['FITNESS_DATABASE_PATH'],'ALLOW_POSE_ONLY_SESSIONS':True,'SESSION_ENGINE_PROVIDER':lambda action:None,'PERSISTENCE_FAULT':fault})


app_module.create_app=testing_app
serve(0)
