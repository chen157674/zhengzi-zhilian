"""管理员本人的轻量实验入口。"""
from flask import request,jsonify,render_template
from flask_login import current_user
from .auth import body,APIError
from .records import uuid_id
from .experiment_sources import ExperimentSources
from .experiments import ExperimentManager


def register_experiments(app,db):
    sources=ExperimentSources();hook=app.config.get('EXPERIMENT_METHOD_HOOK') if app.config.get('TESTING') else None
    manager=ExperimentManager(app.extensions['session_store'],app.extensions['vision_slot'],sources,hook)
    app.extensions['experiment_sources']=sources;app.extensions['experiment_manager']=manager
    app.extensions['auth_revoke_hooks'].append(manager.revoke);app.extensions['start_hooks'].append(manager.recover);app.extensions['stop_hooks'].insert(0,manager.stop)

    @app.get('/api/v1/admin/experiment-options')
    def experiment_options():return jsonify(data=sources.options())

    @app.post('/api/v1/admin/experiments')
    def create_experiment():
        data=body({'task_id','dataset_version','split_hash','method_ids'})
        return jsonify(data=manager.create(current_user.user_id,current_user.id,data,request.headers.get('Idempotency-Key'))),202

    @app.get('/api/v1/admin/experiments')
    def experiments_list():
        if set(request.args)-{'page','page_size'}:raise APIError('INVALID_REQUEST','实验列表参数不合法',400)
        try:page=int(request.args.get('page','1'));size=int(request.args.get('page_size','10'))
        except ValueError as error:raise APIError('VALIDATION_FAILED','分页参数无效',422) from error
        return jsonify(data=manager.list(current_user.user_id,page,size))

    @app.get('/api/v1/admin/experiments/<experiment_id>')
    def experiment_detail(experiment_id):return jsonify(data=manager.snapshot(current_user.user_id,uuid_id(experiment_id)))

    @app.get('/admin/experiments')
    def experiment_page():return render_template('experiments.html',active='',experiment_id='')

    @app.get('/admin/experiments/<experiment_id>')
    def experiment_result_page(experiment_id):
        eid=uuid_id(experiment_id);manager.snapshot(current_user.user_id,eid)
        return render_template('experiments.html',active='',experiment_id=eid)
