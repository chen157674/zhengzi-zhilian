"""本人视频输入接口；只负责输入生命周期，不在请求线程执行整段分析。"""
from tempfile import SpooledTemporaryFile
from flask import Request, current_app, request, jsonify
from flask_login import current_user

from .auth import APIError, body
from .paths import ROOT, project_path
from .records import uuid_id
from .media import MediaInputs, MAX_BYTES
from .presentation_assets import demo_resource,demo_catalogue


class ProjectRequest(Request):
    def _get_file_stream(self,total_content_length,content_type,filename=None,content_length=None):
        if self.path=='/api/v1/account/avatar':
            directory=project_path(current_app.config['AVATAR_SPOOL_ROOT']);directory.mkdir(parents=True,exist_ok=True)
            stream=SpooledTemporaryFile(max_size=512*1024,mode='w+b',dir=directory)
            if not hasattr(self,'_media_streams'):self._media_streams=[]
            self._media_streams.append(stream)
            return stream
        if self.path=='/api/v1/inputs' or self.path.startswith('/api/v1/records/') and self.path.endswith('/source-match'):
            directory=project_path(current_app.config['MEDIA_INPUT_ROOT'])/'spool'
            directory=project_path(directory);directory.mkdir(parents=True,exist_ok=True)
            stream=SpooledTemporaryFile(max_size=512*1024,mode='w+b',dir=directory)
            if not hasattr(self,'_media_streams'):self._media_streams=[]
            self._media_streams.append(stream)
            return stream
        return super()._get_file_stream(total_content_length,content_type,filename,content_length)

    def close(self):
        try:super().close()
        finally:
            # multipart 因部分数／体积拒绝时，Werkzeug 尚未把流放进 request.files。
            for stream in getattr(self,'_media_streams',[]):stream.close()


def register_media(app,db):
    root=project_path(db.path.parent/'media-inputs' if app.config.get('TESTING') else ROOT/'.tmp/video-inputs')
    app.config['MEDIA_INPUT_ROOT']=str(root)
    manager=MediaInputs(app.extensions['session_store'],root,app.extensions['session_store'].clock)
    app.extensions['media_inputs']=manager
    app.extensions['start_hooks'].extend([manager.recover,manager.start])
    app.extensions['auth_revoke_hooks'].append(manager.revoke)
    app.extensions['stop_hooks'].insert(0,manager.stop)

    @app.post('/api/v1/inputs')
    def upload_input():
        if request.mimetype!='multipart/form-data':raise APIError('INVALID_REQUEST','请选择一个视频文件',400)
        if request.form or set(request.files)!={'file'} or len(request.files.getlist('file'))!=1:
            raise APIError('INVALID_REQUEST','每次只能提交一个 file 视频字段',400)
        data=manager.upload(current_user.user_id,current_user.id,request.files['file'].stream,uuid_id(request.headers.get('Idempotency-Key')))
        return jsonify(data=data),202

    @app.get('/api/v1/inputs/<input_id>')
    def get_input(input_id):return jsonify(data=manager.snapshot(current_user.user_id,uuid_id(input_id)))

    @app.get('/api/v1/demos')
    def demos():
        items=[]
        for item in demo_catalogue():
            result=demo_resource(item['id'])
            items.append({'demo_id':item['id'],'action_id':item['action_id'],'title':item['title'],'display_title':item.get('display_title',item['title']),
                'video_url':item['video_url'],'poster_url':item['poster_url'],'saved_result_url':'/video/examples/'+item['id'] if result else None,
                'analysis_track_url':'/static/demo/'+item['id']+'/analysis-track?v='+result['track']['sha256'][:12] if result else None,
                'source_sha256':item['video']['sha256'],'source_type':item.get('source_type','public'),
                'source_label':item.get('source_label','DVIDS / Barbell Bent Over Row'),
                **{k:item.get(k) for k in ('credit','source_url','license','non_endorsement')}})
        return jsonify(data={'demos':items})

    @app.get('/api/v1/voice')
    def voice():
        from .voice_assets import voice_catalogue
        return jsonify(data=voice_catalogue(request.args.get('action_id')))

    @app.post('/api/v1/demo-inputs')
    def demo_input():
        data=body({'demo_id'});item=demo_resource(data.get('demo_id'),require_track=False)
        if not item:raise APIError('DEMO_UNAVAILABLE','请选择训练视频',503)
        with project_path(item['video']['path']).open('rb') as stream:
            result=manager.upload(current_user.user_id,current_user.id,stream,uuid_id(request.headers.get('Idempotency-Key')),demo_id=item['id'])
        return jsonify(data=result),202

    @app.delete('/api/v1/inputs/<input_id>')
    def delete_input(input_id):
        body(set())
        return jsonify(data=manager.release(current_user.user_id,current_user.id,uuid_id(input_id),uuid_id(request.headers.get('Idempotency-Key'))))
