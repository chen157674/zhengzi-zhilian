"""本人AI复盘页面的隔离服务；真实业务存储，供应商为替身，不访问付费接口。"""
import os,sys
from pathlib import Path
from fitness_app.paths import ROOT,configure_environment
configure_environment();sys.path.insert(0,str(ROOT/'tests'))
from flask import jsonify,request
from fitness_app import app as app_module
from fitness_app.runtime import serve
from test_ai_jobs import Provider

assert Path(os.environ['FITNESS_DATABASE_PATH']).parent.resolve()==ROOT/'.tmp/tests/round3-ai-report-ui'
original=app_module.create_app
provider=Provider()
def testing_app(config):
    app=original({**config,'TESTING':True,'DATABASE_PATH':os.environ['FITNESS_DATABASE_PATH']})
    app.extensions['ai_provider_factory']=lambda:provider
    @app.get('/api/v1/_fixture/ai-stats')
    def stats():return jsonify(data={'calls':provider.calls,'paid_provider':False})
    @app.post('/api/v1/_fixture/ai-control')
    def control():
        if request.get_json().get('hold'):provider.release.clear()
        else:provider.release.set()
        return jsonify(data={'configured':True})
    return app
app_module.create_app=testing_app
serve(0)
