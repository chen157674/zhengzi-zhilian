"""自愿保存的骨架分块：本人范围、有限体积、不可复生的删除/到期状态。"""
import hashlib,json,math,threading,zlib,logging
from flask import jsonify,request
from flask_login import current_user
from sqlalchemy import select,update,delete,func
from . import db_schema as s
from .auth import APIError,body
from .records import uuid_id,integer
from .persistence import STORAGE_FAILURES
from .database import StorageError,ConflictError,json_text,idempotent
from .skeleton import skeleton_snapshot,JOINT_NAMES,CONNECTIONS

VERSION='saved-skeleton-1'
RETENTION_MS=30*24*60*60*1000
MAX_BYTES=8*1024*1024
MAX_USER_BYTES=256*1024*1024
MAX_FRAMES=18000
CHUNK_FRAMES=20
MAX_CHUNK=128*1024
SAMPLE_MS=100
logger=logging.getLogger(__name__)
SKELETON_FAILURES=STORAGE_FAILURES+(ConflictError,APIError,ValueError)


def pack_frame(frame,offset=0,coaching=None):
    stamp=frame.get('captured_ms');seq=frame.get('frame_seq')
    if type(stamp) is not int or type(seq) is not int or stamp<offset or seq<0:return None
    snap=skeleton_snapshot(frame,coaching,time_base='source_ms')
    valid=frame.get('identity_accepted') is not False and frame.get('framing',{}).get('person_count')==1
    joints=[]
    if valid:
        for item in snap['joints']:
            values=[item[k] for k in ('x','y','visibility','presence')]
            if all(type(v) in (int,float) and math.isfinite(v) for v in values) and all(0<=v<=1 for v in values):
                joints.append([item['index'],round(values[0],6),round(values[1],6),round(values[2],3),round(values[3],3)])
    return {'t':stamp-offset,'source_ms':frame.get('source_captured_ms',stamp),'seq':seq,
        'w':frame.get('width'),'h':frame.get('height'),'j':joints,'model':frame.get('model_version'),
        'epoch':frame.get('stream_epoch'),'group':frame.get('set_index',1),
        'reason':None if joints else 'identity_or_parts_unavailable','highlight':snap['highlight'] if joints else None}


class SkeletonStore:
    def __init__(self,sessions,fault=None):self.sessions=sessions;self.db=sessions.db;self.clock=sessions.clock;self.fault=fault
    def _fault(self,point):
        if self.fault:self.fault(point)
    def expire(self,c=None,uid=None):
        if c is None:
            with self.db.transaction() as connection:return self.expire(connection,uid)
        conditions=[s.skeleton_tracks.c.expires_at_ms<=self.clock(),s.skeleton_tracks.c.status.not_in(('expired','deleted'))]
        if uid:conditions.append(s.skeleton_tracks.c.user_id==uid)
        ids=select(s.skeleton_tracks.c.id).where(*conditions)
        c.execute(delete(s.skeleton_chunks).where(s.skeleton_chunks.c.track_id.in_(ids)))
        return c.execute(update(s.skeleton_tracks).where(*conditions).values(status='expired',data_bytes=0,frame_count=0,visible_frame_count=0,chunk_count=0,last_ms=-1,reason='retention_elapsed',state_version=s.skeleton_tracks.c.state_version+1)).rowcount
    def _ensure(self,c,uid,sid,offset):
        session=self.sessions._session(c,uid,sid);config=json.loads(session['config_json'])
        if config.get('save_skeleton') is not True:return None
        row=c.execute(select(s.skeleton_tracks).where(s.skeleton_tracks.c.id==sid,s.skeleton_tracks.c.user_id==uid)).mappings().first()
        if row:return row
        metadata={'schema_version':1,'policy_version':VERSION,'coordinate_space':'image_normalized','origin':'top_left','mirrored':False,
            'side_basis':'person_anatomical','depth_is_metric':False,'no_rgb_or_audio_saved':True,'interpolation':'none',
            'time_base':'session_elapsed_ms','source_offset_ms':offset,'source':session['source'],'sample_minimum_ms':SAMPLE_MS,
            'action_id':session['action_id'],'capability_version':session['capability_version'],'posture_policy_version':config.get('posture_policy_version'),
            'video_revision_id':config.get('video_revision_id'),'source_sha256':c.scalar(select(s.media_inputs.c.sha256).where(s.media_inputs.c.id==config.get('input_id'),s.media_inputs.c.user_id==uid)) if config.get('input_id') else None,
            'joint_columns':['index','x','y','visibility','presence'],'joint_names':JOINT_NAMES,'connections':CONNECTIONS}
        now=self.clock();c.execute(s.skeleton_tracks.insert().values(id=sid,user_id=uid,metadata_json=json_text(metadata),created_at_ms=now,expires_at_ms=now+RETENTION_MS))
        return c.execute(select(s.skeleton_tracks).where(s.skeleton_tracks.c.id==sid)).mappings().one()
    def append(self,uid,sid,index,frames,offset):
        if not frames or len(frames)>CHUNK_FRAMES:raise ValueError('骨架批次大小无效')
        raw=json_text({'frames':frames}).encode();payload=zlib.compress(raw,6)
        if len(raw)>MAX_CHUNK or len(payload)>MAX_CHUNK:raise ValueError('骨架批次过大')
        digest=hashlib.sha256(payload).hexdigest();times=[f['t'] for f in frames]
        if any(type(t) is not int or t<0 for t in times) or any(a>=b for a,b in zip(times,times[1:])):raise ValueError('骨架时间未递增')
        with self.db.transaction() as c:
            self.expire(c,uid);track=self._ensure(c,uid,sid,offset)
            if track is None:return 'not_saved'
            existing=c.execute(select(s.skeleton_chunks.c.sha256).where(s.skeleton_chunks.c.track_id==sid,s.skeleton_chunks.c.chunk_index==index)).scalar_one_or_none()
            if existing:
                if existing!=digest:raise ConflictError('同一骨架批次内容不同')
                return track['status']
            if track['status']!='active':return track['status']
            if track['chunk_count']!=index or times[0]<=track['last_ms']:raise ConflictError('骨架批次顺序不一致')
            total=c.scalar(select(func.coalesce(func.sum(s.skeleton_tracks.c.data_bytes),0)).where(s.skeleton_tracks.c.user_id==uid))
            limited=track['data_bytes']+len(payload)>MAX_BYTES or track['frame_count']+len(frames)>MAX_FRAMES or total+len(payload)>MAX_USER_BYTES
            if limited:
                c.execute(update(s.skeleton_tracks).where(s.skeleton_tracks.c.id==sid).values(status='limited',reason='storage_limit',state_version=track['state_version']+1));return 'limited'
            c.execute(s.skeleton_chunks.insert().values(track_id=sid,chunk_index=index,first_ms=times[0],last_ms=times[-1],payload=payload,sha256=digest))
            c.execute(update(s.skeleton_tracks).where(s.skeleton_tracks.c.id==sid).values(data_bytes=track['data_bytes']+len(payload),frame_count=track['frame_count']+len(frames),visible_frame_count=track['visible_frame_count']+sum(bool(v['j']) for v in frames),chunk_count=index+1,last_ms=times[-1],state_version=track['state_version']+1))
            self._fault('before_commit')
        self._fault('after_commit');return 'active'
    def finish(self,uid,sid,status='complete',reason=None):
        with self.db.transaction() as c:
            self.sessions._session(c,uid,sid)
            c.execute(update(s.skeleton_tracks).where(s.skeleton_tracks.c.id==sid,s.skeleton_tracks.c.user_id==uid,s.skeleton_tracks.c.status=='active').values(status=status,reason=reason,state_version=s.skeleton_tracks.c.state_version+1))
    def summary(self,uid,sid,c=None):
        if c is None:
            with self.db.transaction() as connection:return self.summary(uid,sid,connection)
        if c is not None:
            session=self.sessions._session(c,uid,uuid_id(sid));self.expire(c,uid)
            row=c.execute(select(s.skeleton_tracks).where(s.skeleton_tracks.c.id==sid,s.skeleton_tracks.c.user_id==uid)).mappings().first()
            if not row:return {'status':'not_saved','reason':'no_saved_frames' if json.loads(session['config_json']).get('save_skeleton') else 'not_enabled','frame_count':0,'visible_frame_count':0,'state_version':0,'expires_at_ms':None,'last_ms':-1}
            if row['status']=='active' and session['ended_at_ms'] is not None and self.clock()-session['ended_at_ms']>=5000:
                c.execute(update(s.skeleton_tracks).where(s.skeleton_tracks.c.id==sid).values(status='interrupted',reason='closing_unconfirmed',state_version=row['state_version']+1))
                row={**row,'status':'interrupted','reason':'closing_unconfirmed','state_version':row['state_version']+1}
            return {k:row[k] for k in ('status','reason','frame_count','visible_frame_count','data_bytes','last_ms','state_version','created_at_ms','expires_at_ms')}|{'metadata':json.loads(row['metadata_json']),'maximum_bytes':MAX_BYTES,'maximum_frames':MAX_FRAMES}
    def read(self,uid,sid,after=-1,limit=300):
        integer(after,'after_ms',-1);integer(limit,'limit',1,300)
        with self.db.transaction() as c:
            result=self.summary(uid,sid,c);result.update(frames=[],next_after_ms=None)
            if result['status'] in ('not_saved','deleted','expired'):return result
            self.sessions._session(c,uid,sid)
            chunks=c.execute(select(s.skeleton_chunks).join(s.skeleton_tracks,s.skeleton_tracks.c.id==s.skeleton_chunks.c.track_id).where(s.skeleton_tracks.c.id==sid,s.skeleton_tracks.c.user_id==uid,s.skeleton_chunks.c.last_ms>after).order_by(s.skeleton_chunks.c.chunk_index).limit(limit//CHUNK_FRAMES+2)).mappings().all()
            for row in chunks:
                data=row['payload']
                try:
                    if hashlib.sha256(data).hexdigest()!=row['sha256']:raise ValueError('hash')
                    decoder=zlib.decompressobj();raw=decoder.decompress(data,MAX_CHUNK+1)
                    if len(raw)>MAX_CHUNK or not decoder.eof or decoder.unused_data:raise ValueError('size')
                    frames=json.loads(raw)['frames']
                    result['frames'].extend(frame for frame in frames if frame['t']>after)
                except (ValueError,KeyError,TypeError,zlib.error):raise APIError('SKELETON_DATA_UNAVAILABLE','已保存骨架数据未能通过完整性校验',503) from None
                if len(result['frames'])>=limit:break
            result['frames']=result['frames'][:limit]
            if result['frames'] and result['frames'][-1]['t']<result['last_ms']:result['next_after_ms']=result['frames'][-1]['t']
        return result
    def remove(self,uid,auth,sid,version,key):
        sid=uuid_id(sid);integer(version,'expected_version')
        with self.db.transaction() as c:
            self.sessions._actor(c,uid,auth);self.sessions._session(c,uid,sid)
            def write():
                row=c.execute(select(s.skeleton_tracks).where(s.skeleton_tracks.c.id==sid,s.skeleton_tracks.c.user_id==uid)).mappings().first()
                if row and row['state_version']!=version:raise ConflictError('骨架数据已更新，请读取后再删除')
                c.execute(delete(s.skeleton_chunks).where(s.skeleton_chunks.c.track_id==sid))
                if row:c.execute(update(s.skeleton_tracks).where(s.skeleton_tracks.c.id==sid).values(status='deleted',reason='user_deleted',data_bytes=0,frame_count=0,visible_frame_count=0,chunk_count=0,last_ms=-1,state_version=row['state_version']+1))
                return {'deleted':True,'training_summary_retained':True}
            return idempotent(c,uid,uuid_id(key),{'expected_version':version},'skeleton.delete',write,sid)


class SkeletonBuffer:
    def __init__(self,store,uid,sid=None,offset=0):
        self.store,self.uid,self.sid,self.offset=store,uid,sid,offset;self.frames=[];self.index=0;self.last=-1;self.status='buffering';self.reason=None
    def push(self,frame,coaching=None):
        if self.status not in ('active','buffering'):return
        packed=pack_frame(frame,self.offset,coaching)
        if packed is None or packed['t']-self.last<SAMPLE_MS and self.last>=0:return
        if self.sid is None and len(self.frames)>=300:self.status='limited';self.reason='pre_record_buffer_limit';return
        self.frames.append(packed);self.last=packed['t']
        if self.sid and len(self.frames)>=CHUNK_FRAMES:self.flush()
    def bind(self,sid):self.sid=sid;self.flush()
    def flush(self):
        if not self.sid or self.status not in ('active','buffering','limited'):return
        limited=self.status=='limited'
        while self.frames:
            batch=self.frames[:CHUNK_FRAMES]
            try:status=self.store.append(self.uid,self.sid,self.index,batch,self.offset)
            except SKELETON_FAILURES as error:
                # 一次用相同身份查询/重放以核对提交不确定性，不重新运行姿态/分类。
                try:status=self.store.append(self.uid,self.sid,self.index,batch,self.offset)
                except SKELETON_FAILURES:
                    self.status='save_failed';self.reason=type(error).__name__
                    logger.warning('骨架保存停止 record=%s type=%s',self.sid,type(error).__name__)
                    try:self.store.finish(self.uid,self.sid,'save_failed','storage_failure')
                    except SKELETON_FAILURES:logger.warning('骨架故障状态待恢复 record=%s',self.sid)
                    return
            self.status=status
            if status!='active':self.frames.clear();return
            self.frames=self.frames[len(batch):];self.index+=1
        if limited:
            self.status='limited'
            try:self.store.finish(self.uid,self.sid,'limited',self.reason)
            except SKELETON_FAILURES:self.status='save_failed';self.reason='limit_state_unconfirmed';logger.warning('骨架上限状态待恢复 record=%s',self.sid)
    def close(self,interrupted=False):
        self.flush()
        if self.sid and self.status in ('active','buffering'):
            try:self.store.finish(self.uid,self.sid,'interrupted' if interrupted else 'complete');self.status='interrupted' if interrupted else 'complete'
            except SKELETON_FAILURES:self.status='save_failed';self.reason='finalization_failed'
        self.frames.clear()
    def snapshot(self):return {'status':self.status,'reason':self.reason,'pending_frames':len(self.frames)}


def register_skeletons(app):
    store=SkeletonStore(app.extensions['session_store']);app.extensions['skeleton_store']=store;stop=threading.Event();workers=[]
    def start():
        if workers:return
        store.expire()
        with store.db.transaction() as c:
            c.execute(update(s.skeleton_tracks).where(s.skeleton_tracks.c.status=='active').values(status='interrupted',reason='service_restarted',state_version=s.skeleton_tracks.c.state_version+1))
        def run():
            while not stop.wait(60):
                try:store.expire()
                except STORAGE_FAILURES:app.logger.warning('骨架到期清理待重试')
        if not workers:
            thread=threading.Thread(target=run,name='skeleton-expiry',daemon=True);workers.append(thread);thread.start()
    def close():
        stop.set()
        for thread in workers:thread.join(2)
    app.extensions['start_hooks'].append(start);app.extensions['stop_hooks'].append(close)
    @app.route('/api/v1/records/<session_id>/skeleton',methods=['GET','DELETE'])
    def skeleton_record(session_id):
        sid=uuid_id(session_id)
        if request.method=='DELETE':
            data=body({'expected_version'});return jsonify(data=store.remove(current_user.user_id,current_user.id,sid,data.get('expected_version'),request.headers.get('Idempotency-Key')))
        if set(request.args)-{'after_ms','limit'}:raise APIError('INVALID_REQUEST','骨架分页字段无效',400)
        try:after=int(request.args.get('after_ms','-1'));limit=int(request.args.get('limit','300'))
        except ValueError:raise APIError('VALIDATION_FAILED','骨架分页参数无效',422) from None
        return jsonify(data=store.read(current_user.user_id,sid,after,limit))
