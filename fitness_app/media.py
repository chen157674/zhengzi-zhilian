"""本地视频输入、源时间解码及其专属临时副本。"""
from __future__ import annotations

import hashlib
import json
import logging
import threading

from sqlalchemy import select, update

from . import db_schema as s
from .auth import APIError
from .database import idempotent, json_text, StorageError
from .paths import ROOT, project_path
from .records import uuid_id

MAX_BYTES = 1024**3
MAX_DURATION_MS = 30*60*1000
INPUT_TTL_MS = 30*60*1000
logger = logging.getLogger(__name__)


def copy_input(source, destination, maximum=MAX_BYTES):
    """读取器边界可用短小夹具验证；不信任 Content-Length 或文件扩展名。"""
    size=0; digest=hashlib.sha256()
    while True:
        chunk=source.read(min(1024*1024, maximum-size+1))
        if not chunk:break
        size+=len(chunk)
        if size>maximum:raise APIError('INPUT_TOO_LARGE','请选择1 GiB以内的视频',413)
        destination.write(chunk);digest.update(chunk)
    if not size:raise APIError('UNDECODABLE_MEDIA','所选文件为空',422)
    return size,digest.hexdigest()


def source_stamp(frame, origin):
    if frame.pts is None or frame.time_base is None:raise APIError('MEDIA_TIMESTAMPS','视频缺少可用的源时间戳',422)
    stamp=round(float(frame.pts*frame.time_base)*1000)-origin
    if stamp<0 or stamp>MAX_DURATION_MS:raise APIError('MEDIA_DURATION','视频时间无效或超过 30 分钟',422)
    return stamp


def display_rgb(frame):
    """转正和等比缩放仅作用于内存推理帧，不转码或保存用户原片。"""
    import numpy as np
    rotation=frame.rotation
    if rotation%90:raise APIError('UNSUPPORTED_MEDIA','请将视频旋转为0、90、180或270度后上传',415)
    scale=min(1,1280/max(frame.width,frame.height),720/min(frame.width,frame.height))
    width=max(1,round(frame.width*scale));height=max(1,round(frame.height*scale))
    rgb=frame.reformat(width=width,height=height,format='rgb24').to_ndarray()
    return np.ascontiguousarray(np.rot90(rgb,rotation//90))


def probe_video(path):
    import av
    try:
        with av.open(str(path),options={'protocol_whitelist':'file'}) as container:
            videos=list(container.streams.video)
            if 'mp4' not in container.format.name or container.metadata.get('major_brand','').strip() in {'','qt'} or len(videos)!=1:
                raise APIError('UNSUPPORTED_MEDIA','请选择包含单一视频轨道的 MP4 文件',415)
            stream=videos[0]
            if stream.codec_context.name!='h264':raise APIError('UNSUPPORTED_MEDIA','目前支持 MP4 / H.264 8-bit',415)
            duration=round(float(stream.duration*stream.time_base)*1000) if stream.duration is not None else round(container.duration/1000) if container.duration is not None else None
            if duration is not None and not 0<duration<=MAX_DURATION_MS:raise APIError('MEDIA_DURATION','视频时长须在 30 分钟以内',422)
            frame=next(container.decode(stream))
            if any(component.bits!=8 for component in frame.format.components):raise APIError('UNSUPPORTED_MEDIA','目前只支持 8-bit 视频',415)
            origin=round(float(stream.start_time*stream.time_base)*1000) if stream.start_time is not None else 0
            source_stamp(frame,origin)
            rgb=display_rgb(frame)
            return {'codec':'h264','pixel_format':frame.format.name,'duration_ms':duration,'width':int(rgb.shape[1]),'height':int(rgb.shape[0]),
                'source_width':frame.width,'source_height':frame.height,'rotation':frame.rotation,'origin_ms':origin,
                'validation':'container_and_first_decoded_frame','duration_known':duration is not None}
    except (av.FFmpegError,StopIteration,ValueError,OverflowError) as error:
        raise APIError('UNDECODABLE_MEDIA','请重新选择可播放的视频文件',422) from error


def video_frames(path,info,*,start_ms=0,end_ms=None):
    """逐帧有界解码。损坏或时间倒退直接失败，不能报告已完成全片。"""
    import av
    if type(start_ms) is not int or start_ms<0 or end_ms is not None and (type(end_ms) is not int or not start_ms<end_ms<=MAX_DURATION_MS):
        raise APIError('VALIDATION_FAILED','视频源时间范围无效',422)
    previous=-1
    try:
        with av.open(str(path),options={'protocol_whitelist':'file'}) as container:
            if start_ms:
                stream=container.streams.video[0]
                container.seek(int((info['origin_ms']+start_ms)/1000/stream.time_base),stream=stream,backward=True,any_frame=False)
            for frame in container.decode(video=0):
                stamp=source_stamp(frame,info['origin_ms'])
                if stamp<=previous:raise APIError('MEDIA_TIMESTAMPS','源视频时间戳不递增，已停止分析',422)
                if frame.rotation!=info['rotation'] or any(component.bits!=8 for component in frame.format.components):
                    raise APIError('UNSUPPORTED_MEDIA','视频中途改变了方向或位深，已停止分析',415)
                previous=stamp
                if stamp<start_ms:continue
                if end_ms is not None and stamp>=end_ms:break
                yield stamp,display_rgb(frame)
    except av.FFmpegError as error:raise APIError('UNDECODABLE_MEDIA','视频中途解码失败，保留已处理范围',422) from error


class MediaInputs:
    def __init__(self,store,root,clock=s.now_ms,probe=probe_video):
        self.store=store;self.db=store.db;self.root=project_path(root);self.clock=clock;self.probe=probe
        self.lock=threading.RLock();self.threads={};self.closed=False
        self._stop=threading.Event();self._reaper=None

    def _path(self,input_id):
        # 必须与数据库登记的 UUID 路径一致；不接受客户端原始文件名或任意路径。
        expected=self.root/uuid_id(input_id)/'input.mp4'
        resolved=project_path(expected)
        if resolved!=expected.absolute():raise StorageError('视频临时路径存在重定向，拒绝操作')
        resolved.relative_to(self.root)
        return resolved

    def _row(self,c,user_id,input_id):
        row=c.execute(select(s.media_inputs).where(s.media_inputs.c.id==input_id,s.media_inputs.c.user_id==user_id)).mappings().first()
        if not row:raise APIError('NOT_FOUND','视频输入不存在',404)
        return row

    def snapshot(self,user_id,input_id):
        with self.db.transaction(False) as c:row=self._row(c,user_id,input_id)
        info=json.loads(row['encoding_json'] or '{}')
        return {'input_id':row['id'],'status':row['status'],'bytes':row['bytes'],'sha256':row['sha256'],'duration_ms':row['duration_ms'],
            'expires_at_ms':row['expires_at_ms'],'encoding':{k:v for k,v in info.items() if k!='owner_auth_session_id'}}

    def upload(self,user_id,auth_id,stream,command_id,demo_id=None):
        demo=None
        if demo_id is not None:
            from .presentation_assets import demo_resource
            demo=demo_resource(demo_id,require_track=False)
            if not demo:raise APIError('DEMO_UNAVAILABLE','请选择训练视频',503)
        input_id=s.new_id();path=self._path(input_id);kept=False
        with self.lock:
            if self.closed:raise APIError('INVALID_STATE','服务正在停止',409)
            if any(thread.is_alive() for thread in self.threads.values()):raise APIError('RESOURCE_BUSY','正在校验另一视频，请稍后重试',409)
            path.parent.mkdir(parents=True,exist_ok=False)
            try:
                with path.open('xb') as output:size,digest=copy_input(stream,output)
                if demo and (size!=demo['video']['bytes'] or digest!=demo['video']['sha256']):raise APIError('SOURCE_MISMATCH','演示副本与登记原片不一致',422)
                provenance={'owner_auth_session_id':auth_id}
                if demo:provenance['demo_id']=demo_id
                with self.db.transaction() as c:
                    self.store._actor(c,user_id,auth_id)
                    def write():
                        c.execute(s.media_inputs.insert().values(id=input_id,user_id=user_id,bytes=size,sha256=digest,
                            temporary_relative_path=path.relative_to(ROOT).as_posix(),encoding_json=json_text(provenance),
                            created_at_ms=self.clock(),expires_at_ms=self.clock()+INPUT_TTL_MS))
                        return {'id':input_id}
                    payload={'sha256':digest,'bytes':size}
                    if demo:payload['demo_id']=demo_id
                    receipt=idempotent(c,user_id,uuid_id(command_id),payload,'input.create',write)
                kept=receipt['id']==input_id
                if kept:
                    thread=threading.Thread(target=self._validate,args=(user_id,auth_id,input_id),name='fitness-media-validation',daemon=True)
                    self.threads[input_id]=thread;thread.start()
                return self.snapshot(user_id,receipt['id'])
            finally:
                if not kept:self._remove_file(input_id)

    def _remove_file(self,input_id):
        path=self._path(input_id)
        if path.exists():path.unlink()
        if path.parent.exists():path.parent.rmdir()

    def _validate(self,user_id,auth_id,input_id):
        try:
            info=self.probe(self._path(input_id));info['owner_auth_session_id']=auth_id
            with self.lock,self.db.transaction() as c:
                row=self._row(c,user_id,input_id)
                self.store._actor(c,user_id,auth_id)
                if row['status']!='validating':return
                provenance=json.loads(row['encoding_json'] or '{}')
                if provenance.get('demo_id'):info['demo_id']=provenance['demo_id']
                c.execute(update(s.media_inputs).where(s.media_inputs.c.id==input_id).values(status='ready',duration_ms=info['duration_ms'],encoding_json=json_text(info)))
        except Exception as error:
            code=error.code if isinstance(error,APIError) else 'MEDIA_VALIDATION_FAILED'
            message=error.message if isinstance(error,APIError) else '视频校验未完成，请重新选择；可用 request_id 排查'
            try:
                with self.lock,self.db.transaction() as c:
                    row=self._row(c,user_id,input_id)
                    if row['status']=='validating':c.execute(update(s.media_inputs).where(s.media_inputs.c.id==input_id).values(status='rejected',encoding_json=json_text({'error_code':code,'message':message})))
                self.cleanup(user_id,input_id,preserve_rejection=True)
            except Exception as cleanup_error:logger.error('视频校验收尾失败 input=%s type=%s',input_id,type(cleanup_error).__name__)

    def cleanup(self,user_id,input_id,preserve_rejection=False,allow_consumed=False):
        with self.lock,self.db.transaction() as c:
            row=self._row(c,user_id,input_id)
            if row['status']=='consumed' and not allow_consumed:return False
            if row['temporary_relative_path']:
                expected=self._path(input_id).relative_to(ROOT).as_posix()
                if row['temporary_relative_path']!=expected:raise StorageError('视频临时路径与归属不匹配，未清理')
                self._remove_file(input_id)
            changes={'temporary_relative_path':None}
            if not preserve_rejection:changes['status']='cleaned'
            c.execute(update(s.media_inputs).where(s.media_inputs.c.id==input_id).values(**changes))
            return True

    def release(self,user_id,auth_id,input_id,command_id):
        with self.lock:
            with self.db.transaction() as c:
                row=self._row(c,user_id,input_id);self.store._actor(c,user_id,auth_id)
                if row['status']=='consumed':raise APIError('INVALID_STATE','视频正由分析任务持有',409)
                if self.threads.get(input_id) and self.threads[input_id].is_alive():raise APIError('RESOURCE_BUSY','文件校验仍在释放，请稍后再移除',409)
                idempotent(c,user_id,uuid_id(command_id),{},'input.release',lambda:{'id':input_id},input_id)
            if not self.cleanup(user_id,input_id):raise APIError('INVALID_STATE','视频已由分析任务持有',409)
            return self.snapshot(user_id,input_id)

    def recover(self):
        # 重启不续算。只遍历数据库确切登记的未消费副本，不扫描并清空共用目录。
        with self.db.transaction(False) as c:rows=c.execute(select(s.media_inputs).where(s.media_inputs.c.temporary_relative_path.is_not(None))).mappings().all()
        for row in rows:self.cleanup(row['user_id'],row['id'],allow_consumed=True)

    def expire(self):
        with self.db.transaction(False) as c:
            rows=c.execute(select(s.media_inputs).where(s.media_inputs.c.status.in_({'ready','rejected','validating'}),s.media_inputs.c.expires_at_ms<=self.clock(),s.media_inputs.c.temporary_relative_path.is_not(None))).mappings().all()
        for row in rows:
            if not (self.threads.get(row['id']) and self.threads[row['id']].is_alive()):self.cleanup(row['user_id'],row['id'])

    def start(self):
        if self._reaper:return
        def sweep():
            while not self._stop.wait(30):
                try:self.expire()
                except Exception as error:logger.error('过期输入清理未完成 type=%s',type(error).__name__)
        self._reaper=threading.Thread(target=sweep,name='fitness-input-expiry',daemon=True);self._reaper.start()

    def revoke(self,user_id,auth_session_id=None):
        with self.db.transaction(False) as c:
            rows=c.execute(select(s.media_inputs).where(s.media_inputs.c.user_id==user_id,s.media_inputs.c.status.in_({'ready','rejected'}),s.media_inputs.c.temporary_relative_path.is_not(None))).mappings().all()
        for row in rows:
            if auth_session_id is None or json.loads(row['encoding_json'] or '{}').get('owner_auth_session_id')==auth_session_id:self.cleanup(user_id,row['id'])

    def stop(self):
        self._stop.set()
        if self._reaper:self._reaper.join(timeout=1)
        with self.lock:self.closed=True;threads=list(self.threads.values())
        for thread in threads:thread.join(timeout=2)
        if any(thread.is_alive() for thread in threads):raise StorageError('视频校验仍在释放，请稍后停止')
        self.recover()
        spool=self.root/'spool'
        if spool.exists() and not any(spool.iterdir()):spool.rmdir()
        if self.root.exists() and not any(self.root.iterdir()):self.root.rmdir()
