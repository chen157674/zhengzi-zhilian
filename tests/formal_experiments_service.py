"""13 浏览器服务：真实冻结结果；屏障／故障只在隔离测试进程启用。"""
import json
import os
from pathlib import Path
from fitness_app import app as app_module
from fitness_app.auth import initialize_admin,password_hash
from fitness_app.runtime import serve
from fitness_app.paths import ROOT
from fitness_app import db_schema as s

temporary=Path(os.environ['FITNESS_DATABASE_PATH']).parent.resolve();assert temporary==ROOT/'.tmp/tests/ui-step13'
original=app_module.create_app
bootstrap=original({'TESTING':True,'DATABASE_PATH':os.environ['FITNESS_DATABASE_PATH']})
db=bootstrap.extensions['database'];initialize_admin(db,'Experiment_Admin','experiment-fixture-password-123')
with db.transaction() as c:c.execute(s.users.insert().values(username_display='Experiment_User',username_key='experiment_user',password_hash=password_hash('experiment-fixture-password-123')))
db.dispose()


def flags():
    path=temporary/'mode.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}


def method_hook(method,stop):
    while flags().get('block')==method:
        if stop.wait(.05):return
    if flags().get('fail')==method:raise ValueError('D-FAULT selected method')


def testing_app(config):return original({**config,'TESTING':True,'DATABASE_PATH':os.environ['FITNESS_DATABASE_PATH'],'EXPERIMENT_METHOD_HOOK':method_hook})


app_module.create_app=testing_app
serve(0)
