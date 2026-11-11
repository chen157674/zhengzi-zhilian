"""界面协议替身：真实业务/账本，无供应商连接。"""
import os,sys
from pathlib import Path
from fitness_app.paths import ROOT,configure_environment
configure_environment();sys.path.insert(0,str(ROOT/'tests'))
from fitness_app import app as module
from fitness_app.runtime import serve
from test_coach_tasks import Provider
assert Path(os.environ['FITNESS_DATABASE_PATH']).parent.resolve()==ROOT/'.tmp/tests/coach-ui'
original=module.create_app
provider=Provider()
def create(config):
    app=original(config);app.extensions['coach_provider_factory']=lambda:provider;return app
module.create_app=create
serve(0)
