"""本人纠错试标；白名单视频、乐观版本、原片只读、人工来源与未知分开。"""
import copy
import json
from flask import jsonify, render_template
from flask_login import current_user
from .auth import APIError, body
from .database import maintenance_lock, json_text, ConflictError
from .paths import project_path
from .presentation_assets import demo_catalogue, demo_resource
from .media import probe_video
from .records import integer, uuid_id
from .research_extract import atomic_json
from .db_schema import now_ms

ERRORS = [('E01','身体侧倾'),('E02','前后摆动'),('E03','偏离座椅中线'),('E04','明显折腕'),
    ('E05','肘腕与把手方向不匹配'),('E06','偏离握持区'),('E07','闭合握持不符'),('E08','握向与变式不符'),
    ('E09','左右握持位置不一致'),('E10','双侧不同步'),('E11','幅度不足'),('E12','回程过急'),
    ('E13','上臂摆动'),('E14','支撑时髋部偏高或下沉'),('E15','膝在动作中内移'),('E16','脚跟离地')]
FIELDS = {'id','expected_version','demo_id','start_ms','end_ms','error_code','verdict','side','phase','note'}
DATA_FIELDS = FIELDS-{'expected_version'}
ERRORS_BY_ACTION={'machine_chest_press':{f'E{i:02}' for i in range(1,13)},
                 'pec_deck_fly':{f'E{i:02}' for i in range(1,13)}-{'E05'}}


class CorrectionLabels:
    def __init__(self, sessions):
        self.sessions=sessions
        self.root=project_path(sessions.db.path.parent/'private/correction-labels')

    def owner(self, uid, auth):
        with self.sessions.db.transaction(False) as c:
            self.sessions._actor(c,uid,auth)

    def path(self, uid):
        return project_path(self.root/(uuid_id(uid)+'.json'))

    def read(self, uid):
        path=self.path(uid)
        if not path.exists():return {'schema_version':1,'owner_id':uid,'version':0,'records':[]}
        value=json.loads(path.read_text(encoding='utf-8'))
        if value.get('owner_id')!=uid or value.get('schema_version')!=1:
            raise ValueError('标签文件身份不符')
        return value

    def get(self, uid, auth):
        self.owner(uid,auth)
        return self.read(uid)

    def save(self, uid, auth, data):
        self.owner(uid,auth)
        uid=uuid_id(uid);rid=uuid_id(data.get('id'));version=integer(data.get('expected_version'),'expected_version')
        if set(data)!=FIELDS:raise APIError('VALIDATION_FAILED','标注字段不完整',422)
        if any(not isinstance(data[k],str) for k in ('demo_id','error_code','verdict','side','phase')):
            raise APIError('VALIDATION_FAILED','标注选项必须为文本',422)
        if data['error_code'] not in dict(ERRORS) or data['verdict'] not in ('present','absent','unknown'):
            raise APIError('VALIDATION_FAILED','请选择问题和判断',422)
        if data['side'] not in ('left','right','both','none','unknown') or data['phase'] not in ('work','setup','rest','unknown'):
            raise APIError('VALIDATION_FAILED','侧别或阶段无效',422)
        note=data['note']
        if not isinstance(note,str) or len(note)>300:raise APIError('VALIDATION_FAILED','备注最多300字',422)
        demo=demo_resource(data.get('demo_id'),require_track=False)
        if not demo:raise APIError('VALIDATION_FAILED','视频不在可标注清单',422)
        if data['error_code'] not in ERRORS_BY_ACTION.get(demo['action_id'],set()):
            raise APIError('VALIDATION_FAILED','该检查项目不适用于当前动作',422)
        start=integer(data['start_ms'],'start_ms',0,1800000);end=integer(data['end_ms'],'end_ms',1,1800000)
        info=probe_video(project_path(demo['video']['path']))
        if not start<end<=info['duration_ms']:raise APIError('VALIDATION_FAILED','请在视频范围内选择有效起止时间',422)
        payload={key:data[key] for key in DATA_FIELDS}
        payload['id']=rid
        path=self.path(uid);path.parent.mkdir(parents=True,exist_ok=True)
        with maintenance_lock(path):
            value=self.read(uid)
            old=next((r for r in value['records'] if r['id']==rid),None)
            same=next((r for r in value['records'] if all(r.get(k)==v for k,v in payload.items() if k!='id') and r['source_sha256']==demo['video']['sha256']),None)
            if same:return value
            if value['version']!=version:raise ConflictError('标注已更新，请重新加载后再保存')
            if old and len(old['history'])>=50:raise APIError('LABEL_LIMIT','该标注修订已达上限，请先整理',409)
            if not old and len(value['records'])>=1000:raise APIError('LABEL_LIMIT','本批标注数量已达上限',409)
            code=data['error_code'];verdict=data['verdict']
            record={**payload,'source_sha256':demo['video']['sha256'],'action_id':demo['action_id'],
                'assessed_error_codes':[] if verdict=='unknown' else [code],
                'present_error_codes':[code] if verdict=='present' else [],
                'unknown_error_codes':[code] if verdict=='unknown' else [],
                'annotation_source':'user_confirmed','independent_professional_review':False,
                'dataset_role':'development_pending_review','updated_at_ms':now_ms(),
                'revision':old['revision']+1 if old else 1,
                'history':copy.deepcopy(old['history'])+[ {k:v for k,v in old.items() if k!='history'} ] if old else []}
            if old:value['records'][value['records'].index(old)]=record
            else:value['records'].append(record)
            value['version']+=1
            if len(json_text(value).encode('utf-8'))>2*1024*1024:raise APIError('LABEL_LIMIT','本批标注容量已满',409)
            atomic_json(path,value)
            return value


def register_correction_labels(app):
    store=CorrectionLabels(app.extensions['session_store'])
    app.extensions['correction_labels']=store

    @app.get('/admin/correction-labels')
    @app.get('/account/correction-labels')
    def correction_label_page():
        return render_template('correction_labels.html',active='')

    @app.get('/api/v1/admin/correction-labels')
    @app.get('/api/v1/me/correction-labels')
    def correction_labels_get():
        result=store.get(current_user.user_id,current_user.id)
        demos=[{'id':d['id'],'title':d.get('display_title',d['title']),'video_url':d['video_url'],
                'error_codes':sorted(ERRORS_BY_ACTION[d['action_id']])}
               for d in demo_catalogue() if d['action_id'] in ERRORS_BY_ACTION]
        response=jsonify(data={**result,'demos':demos,'errors':[{'code':k,'title':v} for k,v in ERRORS]})
        response.headers['Cache-Control']='no-store'
        return response

    @app.post('/api/v1/admin/correction-labels')
    @app.post('/api/v1/me/correction-labels')
    def correction_labels_save():
        response=jsonify(data=store.save(current_user.user_id,current_user.id,body(FIELDS)))
        response.headers['Cache-Control']='no-store'
        return response
