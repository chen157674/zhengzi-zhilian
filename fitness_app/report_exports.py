"""本人PDF异步导出：冻结、版本身份、24小时清理和下载鉴权，不占视觉槽。"""
from io import BytesIO
import hashlib,json,queue,threading
from flask import jsonify,request,send_file
from flask_login import current_user
from sqlalchemy import select,update,func
from . import db_schema as s
from .auth import APIError,body
from .records import uuid_id,integer
from .database import ConflictError,json_text,command_digest,idempotent
from .export_snapshot import ExportSnapshots,normalize
from .pdf_report import render_pdf,ReportRenderError,ReportRenderCancelled,MAX_PDF_BYTES
from .persistence import STORAGE_FAILURES

TTL_MS=24*60*60*1000
MAX_RETAINED_BYTES=64*1024*1024
ACTIVE=('queued','running')
TERMINAL=('ready','failed','expired','invalidated','deleted')
FIELDS={'record_ids','expected_versions','include_user_notes','include_ai_report','ai_report_ids','format'}
HEADER_COLUMNS=[c for c in s.report_exports.c if c.name not in {'pdf_blob','snapshot_json'}]


class ReportExports:
    def __init__(self,app):
        self.app=app;self.builder=ExportSnapshots(app);self.sessions=self.builder.sessions;self.db=self.sessions.db;self.clock=self.sessions.clock
        self.lock=threading.RLock();self.jobs=queue.Queue(3);self.worker=None;self.sweeper=None;self.closed=threading.Event();self.renderer=app.config.get('PDF_RENDERER',render_pdf) if app.config.get('TESTING') else render_pdf
    def _row(self,c,uid,rid):
        row=c.execute(select(*HEADER_COLUMNS).where(s.report_exports.c.id==rid,s.report_exports.c.user_id==uid)).mappings().first()
        if row is None:raise APIError('NOT_FOUND','报告不存在',404)
        return dict(row)
    def _clear(self,c,row,status,reason):
        changes={'status':status,'error_code':reason,'snapshot_json':None,'pdf_blob':None,'pdf_sha256':None,'data_bytes':0,'state_version':row['state_version']+1}
        c.execute(update(s.report_exports).where(s.report_exports.c.id==row['id'],s.report_exports.c.user_id==row['user_id']).values(**changes));row.update(changes)
    def _refresh(self,c,row):
        if row['expires_at_ms']<=self.clock() and row['status'] not in {'expired','deleted'}:self._clear(c,row,'expired','retention_elapsed')
        stale=False
        if row['status'] in ACTIVE+('ready',):
            reason,stale=self.builder.state(c,row['user_id'],json.loads(row['dependencies_json']))
            if reason:self._clear(c,row,'invalidated',reason)
        return stale
    def _public(self,row,stale=False):
        options=json.loads(row['options_json']);return {k:row[k] for k in ('id','status','state_version','created_at_ms','expires_at_ms','generated_at_ms','data_bytes','error_code')}|{
            'record_ids':options['record_ids'],'record_count':len(options['record_ids']),'include_user_notes':options['include_user_notes'],'ai_report_count':len(options['ai_report_ids']),
            'snapshot_state':'stale' if stale else 'current','download_available':row['status']=='ready','export_does_not_call_ai':True}
    def get(self,uid,auth,rid):
        with self.db.transaction() as c:
            self.sessions._actor(c,uid,auth);row=self._row(c,uid,uuid_id(rid));stale=self._refresh(c,row);return self._public(row,stale)
    def list(self,uid,auth):
        with self.db.transaction() as c:
            self.sessions._actor(c,uid,auth);rows=c.execute(select(*HEADER_COLUMNS).where(s.report_exports.c.user_id==uid).order_by(s.report_exports.c.created_at_ms.desc()).limit(20)).mappings().all()
            return [self._public(row,self._refresh(c,row)) for raw in rows for row in [dict(raw)]]
    def create(self,uid,auth,payload,key):
        payload=normalize(payload);key=uuid_id(key)
        with self.lock:
            if self.closed.is_set():raise APIError('EXPORT_SERVICE_STOPPED','报告服务暂不可用',503)
            with self.db.transaction(False) as c:
                self.sessions._actor(c,uid,auth);old=c.execute(select(s.command_receipts).where(s.command_receipts.c.command_id==key)).mappings().first()
                if old:
                    if old['actor_user_id']!=uid or old['request_digest']!=command_digest('report.export.create',None,payload):raise ConflictError('导出请求标识与内容不一致','IDEMPOTENCY_CONFLICT')
                    rid=json.loads(old['result_json'])['id']
                else:rid=None
            if rid:return self.get(uid,auth,rid)
            snapshot,deps,fingerprint=self.builder.freeze(uid,auth,payload)
            with self.db.transaction() as c:
                self.sessions._actor(c,uid,auth);reason,stale=self.builder.state(c,uid,deps)
                if reason or stale:raise ConflictError('导出依据已变化，请重新核对记录范围')
                def write():
                    now=self.clock();existing=c.execute(select(*HEADER_COLUMNS).where(s.report_exports.c.user_id==uid,s.report_exports.c.fingerprint==fingerprint,s.report_exports.c.expires_at_ms>now,s.report_exports.c.status.in_(ACTIVE+('ready',))).order_by(s.report_exports.c.created_at_ms.desc())).mappings().first()
                    if existing:return {'id':existing['id'],'created':False}
                    active=c.scalar(select(func.count()).select_from(s.report_exports).where(s.report_exports.c.user_id==uid,s.report_exports.c.status.in_(ACTIVE)))
                    if active>=2 or self.jobs.full():raise APIError('EXPORT_BUSY','报告任务较多，请稍后再导出',429)
                    daily=c.scalar(select(func.count()).select_from(s.report_exports).where(s.report_exports.c.user_id==uid,s.report_exports.c.created_at_ms>=now-TTL_MS))
                    retained=c.scalar(select(func.coalesce(func.sum(s.report_exports.c.data_bytes),0)).where(s.report_exports.c.user_id==uid,s.report_exports.c.expires_at_ms>now))
                    if daily>=60 or retained+(active+1)*MAX_PDF_BYTES>MAX_RETAINED_BYTES:raise APIError('EXPORT_LIMIT','报告生成或临时存储达到上限，请清理旧报告或稍后再试',429)
                    rid=s.new_id();c.execute(s.report_exports.insert().values(id=rid,user_id=uid,auth_id=auth,fingerprint=fingerprint,options_json=json_text(payload),dependencies_json=json_text(deps),snapshot_json=json_text(snapshot),created_at_ms=now,expires_at_ms=now+TTL_MS));return {'id':rid,'created':True}
                result=idempotent(c,uid,key,payload,'report.export.create',write)
                row=self._row(c,uid,result['id']);new_job=result.get('created',False)
            if new_job:
                self.jobs.put_nowait(row['id']);self._start()
            return self.get(uid,auth,row['id'])
    def _start(self):
        if self.worker is None:
            self.worker=threading.Thread(target=self._worker,name='fitness-pdf',daemon=True);self.worker.start()
    def _cancelled(self,rid):
        if self.closed.is_set():return True
        with self.db.transaction(False) as c:
            row=c.execute(select(s.report_exports.c.status,s.report_exports.c.user_id,s.report_exports.c.auth_id).where(s.report_exports.c.id==rid)).first()
            if row is None or row.status!='running':return True
            try:self.sessions._actor(c,row.user_id,row.auth_id)
            except APIError:return True
            return False
    def _worker(self):
        while not self.closed.is_set():
            try:rid=self.jobs.get(timeout=.25)
            except queue.Empty:continue
            try:self._execute(rid)
            except Exception as error:
                self.app.logger.warning('报告生成未完成 id=%s type=%s',rid,type(error).__name__)
                try:
                    with self.db.transaction() as c:
                        row=c.execute(select(*HEADER_COLUMNS).where(s.report_exports.c.id==rid)).mappings().first()
                        if row and row['status'] in ACTIVE:self._clear(c,dict(row),'failed','render_failed')
                except STORAGE_FAILURES:self.app.logger.warning('报告故障状态待恢复 id=%s',rid)
            finally:self.jobs.task_done()
    def _execute(self,rid):
        with self.db.transaction() as c:
            row=c.execute(select(s.report_exports).where(s.report_exports.c.id==rid)).mappings().first()
            if row is None or row['status']!='queued':return
            row=dict(row);self._refresh(c,row)
            if row['status']!='queued':return
            try:self.sessions._actor(c,row['user_id'],row['auth_id'])
            except APIError:self._clear(c,row,'failed','login_changed');return
            snapshot=json.loads(row['snapshot_json']);c.execute(update(s.report_exports).where(s.report_exports.c.id==rid).values(status='running',state_version=row['state_version']+1))
        try:pdf=self.renderer(snapshot,rid,row['created_at_ms'],lambda:self._cancelled(rid))
        except ReportRenderCancelled:
            with self.db.transaction() as c:
                current=self._row(c,row['user_id'],rid)
                if current['status']=='running':self._clear(c,current,'failed','cancelled_or_login_changed')
            return
        except ReportRenderError as error:
            with self.db.transaction() as c:
                current=self._row(c,row['user_id'],rid)
                if current['status']=='running':self._clear(c,current,'failed','report_limit' if str(error)=='report_limit' else 'render_resources')
            return
        if not isinstance(pdf,bytes) or not pdf.startswith(b'%PDF-') or not pdf.rstrip().endswith(b'%%EOF') or len(pdf)>10*1024*1024:raise ValueError('invalid_pdf_result')
        with self.db.transaction() as c:
            current=self._row(c,row['user_id'],rid);self._refresh(c,current)
            if self.closed.is_set() or current['status']!='running':return
            try:self.sessions._actor(c,current['user_id'],current['auth_id'])
            except APIError:self._clear(c,current,'failed','login_changed');return
            c.execute(update(s.report_exports).where(s.report_exports.c.id==rid).values(status='ready',pdf_blob=pdf,pdf_sha256=hashlib.sha256(pdf).hexdigest(),data_bytes=len(pdf),generated_at_ms=self.clock(),state_version=current['state_version']+1))
    def download(self,uid,auth,rid):
        error=None;pdf=None;stale=False
        with self.db.transaction() as c:
            self.sessions._actor(c,uid,auth);row=self._row(c,uid,uuid_id(rid));stale=self._refresh(c,row)
            if row['status'] in {'expired','invalidated','deleted'}:error=('EXPORT_UNAVAILABLE','报告已到期、删除或来源失效，请重新核对记录后导出',410)
            elif row['status']!='ready':error=('EXPORT_NOT_READY','报告尚未生成成功',409)
            else:
                pdf=c.scalar(select(s.report_exports.c.pdf_blob).where(s.report_exports.c.id==rid,s.report_exports.c.user_id==uid))
                if hashlib.sha256(pdf).hexdigest()!=row['pdf_sha256']:
                    self._clear(c,row,'invalidated','pdf_integrity_failed');error=('EXPORT_DATA_UNAVAILABLE','报告文件未通过完整性校验',503)
        if error:raise APIError(*error)
        return pdf,stale
    def remove(self,uid,auth,rid,version,key):
        rid=uuid_id(rid);integer(version,'expected_version')
        with self.db.transaction() as c:
            self.sessions._actor(c,uid,auth)
            def write():
                row=self._row(c,uid,rid)
                if row['state_version']!=version:raise ConflictError('报告状态已改变，请读取后再删除')
                self._clear(c,row,'deleted','user_deleted');return {'id':rid,'deleted':True,'training_records_retained':True}
            return idempotent(c,uid,uuid_id(key),{'expected_version':version},'report.export.delete',write,rid)
    def sweep(self):
        with self.db.transaction() as c:
            rows=c.execute(select(*HEADER_COLUMNS).where(s.report_exports.c.status.not_in(('expired','deleted','invalidated')))).mappings().all()
            for row in rows:self._refresh(c,dict(row))
    def recover(self):
        with self.db.transaction() as c:
            rows=c.execute(select(*HEADER_COLUMNS).where(s.report_exports.c.status.in_(ACTIVE))).mappings().all()
            for row in rows:self._clear(c,dict(row),'failed','service_restarted')
        self.sweep()
        if self.sweeper is None:
            def run():
                while not self.closed.wait(60):
                    try:self.sweep()
                    except STORAGE_FAILURES:self.app.logger.warning('报告清理待重试')
            self.sweeper=threading.Thread(target=run,name='pdf-expiry',daemon=True);self.sweeper.start()
    def stop(self):
        self.closed.set()
        if self.worker:self.worker.join(5)
        if self.worker and self.worker.is_alive():raise APIError('EXPORT_STOPPING','报告生成正在停止，请稍后核对服务状态',503)
        if self.sweeper:self.sweeper.join(2)


def register_report_exports(app):
    service=ReportExports(app);app.extensions['report_exports']=service;app.extensions['start_hooks'].append(service.recover);app.extensions['stop_hooks'].insert(0,service.stop)
    @app.route('/api/v1/reports/exports',methods=['POST','GET'])
    def exports():
        if request.method=='POST':return jsonify(data=service.create(current_user.user_id,current_user.id,body(FIELDS),request.headers.get('Idempotency-Key'))),202
        return jsonify(data={'reports':service.list(current_user.user_id,current_user.id)})
    @app.get('/api/v1/reports/export-options')
    def options():
        if set(request.args)!={'record_ids'}:raise APIError('INVALID_REQUEST','请选择要导出的记录',400)
        return jsonify(data=service.builder.options(current_user.user_id,current_user.id,request.args['record_ids'].split(',')))
    @app.route('/api/v1/reports/exports/<report_id>',methods=['GET','DELETE'])
    def export(report_id):
        if request.method=='DELETE':return jsonify(data=service.remove(current_user.user_id,current_user.id,report_id,body({'expected_version'}).get('expected_version'),request.headers.get('Idempotency-Key')))
        return jsonify(data=service.get(current_user.user_id,current_user.id,report_id))
    @app.get('/api/v1/reports/exports/<report_id>/download')
    def download(report_id):
        pdf,stale=service.download(current_user.user_id,current_user.id,report_id)
        response=send_file(BytesIO(pdf),mimetype='application/pdf',as_attachment=True,download_name='知练同行-训练复盘报告.pdf',max_age=0);response.headers['Cache-Control']='no-store, private';response.headers['X-Content-Type-Options']='nosniff';response.headers['X-Report-Snapshot-State']='stale' if stale else 'current';return response
