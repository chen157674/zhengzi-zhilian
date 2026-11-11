"""14-D／E 隔离浏览器服务，默认真实 Full 与正式适配；公开视频回放非现场相机。"""
import os
from pathlib import Path
from flask import send_file
from fitness_app import app as app_module
from fitness_app.auth import initialize_admin
from fitness_app.runtime import serve
from fitness_app.paths import ROOT

assert Path(os.environ['FITNESS_DATABASE_PATH']).parent.resolve()==ROOT/'.tmp/tests/ui-step14'
original=app_module.create_app
bootstrap=original({'TESTING':True,'DATABASE_PATH':os.environ['FITNESS_DATABASE_PATH']})
db=bootstrap.extensions['database'];initialize_admin(db,'Movement_Admin','movement-fixture-password-123');db.dispose()


def testing_app(config):
    app=original({**config,'TESTING':True,'DATABASE_PATH':os.environ['FITNESS_DATABASE_PATH']})
    @app.get('/__fixture/plank.mp4')
    def source_video():return send_file(ROOT/'data/datasets/development-movements/plank-dvids-640237-side.mp4',conditional=True)
    @app.get('/__fixture/jumping-jack.mp4')
    def jack_video():return send_file(ROOT/'data/datasets/development-movements/jumping-jacks-commons-segment.mp4',conditional=True)
    @app.get('/__fixture/barbell-row.mp4')
    def row_video():return send_file(ROOT/'data/datasets/development-movements/barbell-row-dvids-639914-side.mp4',conditional=True)
    return app


app_module.create_app=testing_app
serve(0)
