"""12 浏览器隔离服务：默认真实 Full 与默认周期资源，不注入模拟计数。"""
import os
from pathlib import Path
from flask import send_file
from fitness_app import app as app_module
from fitness_app.auth import initialize_admin,password_hash
from fitness_app.runtime import serve
from fitness_app.paths import ROOT
from fitness_app import db_schema as s

assert Path(os.environ['FITNESS_DATABASE_PATH']).parent.resolve()==ROOT/'.tmp/tests/ui-step12'
original=app_module.create_app
bootstrap=original({'TESTING':True,'DATABASE_PATH':os.environ['FITNESS_DATABASE_PATH']})
db=bootstrap.extensions['database'];initialize_admin(db,'Count_Admin','count-fixture-password-123')
with db.transaction() as c:c.execute(s.users.insert().values(username_display='Count_User',username_key='count_user',password_hash=password_hash('count-fixture-password-123')))
db.dispose()


def testing_app(config):
    app=original({**config,'TESTING':True,'DATABASE_PATH':os.environ['FITNESS_DATABASE_PATH']})
    @app.get('/__fixture/counting.mp4')
    def source_video():return send_file(ROOT/'web/demo.mp4',conditional=True)
    return app


app_module.create_app=testing_app
serve(0)
