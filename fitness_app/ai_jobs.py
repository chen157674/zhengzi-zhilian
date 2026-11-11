"""独立AI任务：本人证据、有限内存对话、一次发送、可取消与明确费用状态。"""
import hashlib,json,queue,threading,time
from flask import request,jsonify
from flask_login import current_user
from sqlalchemy import select,update,func,or_
from . import db_schema as s
from .auth import APIError,body
from .database import ConflictError,StorageError,json_text
from .records import uuid_id,integer
from .ai_context import AIContextBuilder,messages_for,validate_answer,validate_chat_answer,report_display,validate_fitness_question,VISUAL_QUESTION,VISUAL_FORM_QUESTION
from .ai_usage import AIUsageStore,TERMINAL
from .deepseek_client import ProviderError
from .ai_vision import validate_frames,attach_frames,MAX_FRAMES

CONTEXT_FIELDS={'context_kind','context_id','record_ids','expected_versions','history_scope','filters'}
REPORT_EXPECTED_FIELDS={'settings_version','profile_version','history_fingerprint','rules_version'}
TTL_SECONDS=7200


class AIJobs:
    def __init__(self,app):
        self.app=app;self.builder=AIContextBuilder(app);self.usage=AIUsageStore(app.extensions['ai_settings'])
        self.lock=threading.RLock();self.jobs=queue.Queue(8);self.conversations={};self.answers={}
        self.workers=[];self.closed=False;self.ready=True
        try:self.usage.recover()
        except StorageError:
            self.ready=False;app.logger.error('AI任务恢复未完成，视觉功能可继续使用')

    def _actor(self,uid,auth):
        with self.usage.db.transaction(False) as c:self.usage.sessions._actor(c,uid,auth)

    def _prune(self):
        now=time.monotonic()
        for mapping in (self.conversations,self.answers):
            for key,value in list(mapping.items()):
                if now-value['touched']>TTL_SECONDS:mapping.pop(key,None)
        if self.answers:
            # 失败/取消/不确定没有可展示正文；持久复盘从数据库读取，均不占聊天正文槽。
            with self.usage.db.transaction(False) as c:
                removable=c.scalars(select(s.ai_requests.c.id).where(s.ai_requests.c.id.in_(list(self.answers)),
                    s.ai_requests.c.status.in_(TERMINAL),or_(s.ai_requests.c.status!='succeeded',s.ai_requests.c.kind.in_(('review','comparison'))))).all()
            for rid in removable:self.answers.pop(rid,None)

    def _conversation(self,uid,auth,cid):
        self._actor(uid,auth);self._prune();value=self.conversations.get(uuid_id(cid))
        if not value or value['uid']!=uid or value['auth']!=auth:raise APIError('NOT_FOUND','对话已关闭、过期或不属于本次登录',404)
        value['touched']=time.monotonic();return value

    def create(self,uid,auth,cid,context):
        cid=uuid_id(cid);digest=hashlib.sha256(json_text(context).encode()).hexdigest()
        with self.lock:
            self._prune();self._actor(uid,auth)
            if cid in self.conversations:
                old=self._conversation(uid,auth,cid)
                if old['digest']!=digest:raise ConflictError('同一对话标识不能改变上下文','IDEMPOTENCY_CONFLICT')
                return self.conversation(uid,auth,cid)
            if len(self.conversations)>=64 or sum(v['uid']==uid for v in self.conversations.values())>=5:
                raise APIError('AI_BUSY','打开的AI对话较多，请先关闭旧对话',429)
            bundle=self.builder.build(uid,auth,{**context,'text':'请解释本次训练依据'})
            revision=hashlib.sha256(json_text(bundle['reference']).encode()).hexdigest()
            self.conversations[cid]={'uid':uid,'auth':auth,'bundle':bundle,'revision':revision,'digest':digest,
                'messages':[],'touched':time.monotonic()}
            return self.conversation(uid,auth,cid)

    def conversation(self,uid,auth,cid):
        with self.lock:
            value=self._conversation(uid,auth,cid)
            current=self.builder.current(uid,auth,value['bundle']['reference'])
            return {'conversation_id':cid,'context_revision':value['revision'],'context_current':current,
                    'live_context':self.builder.live_context(uid,value['bundle']['reference']) if current else None,
                    'read_scope':value['bundle']['read_scope'],'messages':list(value['messages']) if current else [],
                    'image_input_supported':self.app.extensions['ai_settings'].get(uid,auth)['model']=='deepseek-flash',
                    'image_max_frames':MAX_FRAMES,
                    'retention':'current_login_memory','expires_after_inactive_seconds':TTL_SECONDS,
                    'opening_does_not_call_provider':True}

    def _available(self):
        if self.closed or not self.ready:raise APIError('AI_SERVICE_STOPPED','AI任务服务暂不可用，姿态分析仍可使用',503)

    def _start_workers(self):
        if not self.workers:
            for index in range(2):
                thread=threading.Thread(target=self._worker,name='fitness-ai-'+str(index),daemon=True)
                self.workers.append(thread);thread.start()

    def report_bundle(self,uid,auth,data):
        kind=data.get('kind','review')
        if not isinstance(kind,str) or kind not in {'interpretation','review','comparison'}:raise APIError('VALIDATION_FAILED','报告类型无效',422)
        context={k:v for k,v in data.items() if k in CONTEXT_FIELDS}
        if kind=='comparison':context['context_kind']='comparison'
        elif kind=='review':context.setdefault('context_kind','overview')
        question='请比较所选两条训练的可比检测证据，并给出下一次关注重点。' if kind=='comparison' else '请复盘这些训练记录，并给出下一次关注重点'
        bundle=self.builder.build(uid,auth,{**context,'text':data.get('text') or question})
        if kind=='review':
            from .records import TERMINAL as RECORD_TERMINAL
            if bundle['facts']['live'] is not None or any(v['source']=='demo' or v['included_in_statistics'] is False or v['status'] not in RECORD_TERMINAL or v['started_at_ms'] is None for v in bundle['facts']['selected_records']):
                raise APIError('AI_REVIEW_UNAVAILABLE','复盘需选择已结束的当前有效本人训练；旧修订仍可使用AI解读',409)
        expected=data.get('expected_context')
        if expected is not None:
            if not isinstance(expected,dict) or set(expected)!=REPORT_EXPECTED_FIELDS:raise APIError('VALIDATION_FAILED','复盘依据版本不完整',422)
            if any(expected[k]!=bundle['reference'][k] for k in REPORT_EXPECTED_FIELDS):raise ConflictError('训练范围、档案或AI设置已变化，请重新核对后生成')
        return kind,bundle

    def report_options(self,uid,auth,data):
        kind,bundle=self.report_bundle(uid,auth,data)
        if kind not in {'review','comparison'}:raise APIError('VALIDATION_FAILED','请选择训练复盘或前后对比',422)
        facts=bundle['facts'];history=facts['history'];settings=self.app.extensions['ai_settings'].get(uid,auth)
        records=[{k:v[k] for k in ('session_id','action_id','action_name','started_at_ms','state_version')} for v in facts['selected_records']]
        comparison=facts['comparison']
        return {'kind':kind,'records':records,'record_ids':[v['session_id'] for v in records],
                'expected_versions':{v['session_id']:v['state_version'] for v in records},
                'expected_context':{k:bundle['reference'][k] for k in REPORT_EXPECTED_FIELDS},'read_scope':bundle['read_scope'],
                'history':{'records_total':history['records_total'],'rule_cohorts':history['rule_cohorts'][:5],
                           'details_returned':history['details_returned'],'details_limited':history['details_limited']} if history else None,
                'comparison':{k:comparison[k] for k in ('comparable','comparisons','reason')} if comparison else None,
                'provider':{'source':settings['source'],'model':settings['model'],'available':settings['available'],'reason':settings['reason']},
                'no_generation_performed':True}

    def report_list(self,uid,auth,args):
        if set(args)-{'page','page_size'}:raise APIError('INVALID_REQUEST','报告筛选字段无效',400)
        try:page=int(args.get('page','1'));size=int(args.get('page_size','10'))
        except (TypeError,ValueError):raise APIError('VALIDATION_FAILED','分页无效',422) from None
        if not 1<=page<=100000 or not 1<=size<=20:raise APIError('VALIDATION_FAILED','分页范围无效',422)
        table=s.ai_requests;conditions=[table.c.user_id==uid,table.c.kind.in_(('review','comparison'))]
        with self.usage.db.transaction(False) as c:
            self.usage.sessions._actor(c,uid,auth)
            total=c.scalar(select(func.count()).select_from(table).where(*conditions))
            rows=c.execute(select(table.c.id,table.c.kind,table.c.status,table.c.model,table.c.source,table.c.created_at_ms,
                table.c.estimated_micro_cny,table.c.cancel_requested,table.c.result_json.is_not(None).label('body_saved'))
                .where(*conditions).order_by(table.c.created_at_ms.desc(),table.c.id).limit(size).offset((page-1)*size)).mappings()
            reports=[]
            for row in rows:
                item=dict(row);item['request_id']=item.pop('id')
                if item.pop('cancel_requested') and item['status']=='running':item['status']='cancelling'
                reports.append(item)
        return {'reports':reports,'total':total,'page':page,'page_size':size,'list_does_not_validate_current_context':True}

    def submit(self,uid,auth,rid,data,cid=None):
        rid=uuid_id(rid)
        if data.get('consent') is not True:raise APIError('AI_CONSENT_REQUIRED','请确认将本次只读训练摘要发送至DeepSeek',422)
        visual=data.get('include_current_frame',False)
        if type(visual) is not bool or data.get('frames') and not visual:
            raise APIError('AI_IMAGE_CONSENT_REQUIRED','请确认发送当前画面',422)
        if visual and not cid:raise APIError('AI_IMAGE_UNAVAILABLE','请从AI聊天发送当前画面',422)
        digest=hashlib.sha256(json_text({'conversation_id':cid,'payload':data}).encode()).hexdigest()
        with self.lock:
            self._available()
            existing=self.usage.existing(uid,auth,rid,digest)
            if existing:return self.snapshot(uid,auth,rid)
            self._prune()
            if len(self.answers)>=256:raise APIError('AI_BUSY','本机AI结果缓存已满，请稍后再试',429)
            if cid:
                value=self._conversation(uid,auth,cid)
                if data.get('expected_context_revision')!=value['revision']:raise ConflictError('对话依据已变化，请重新打开AI助理')
                text=data.get('text');self.builder.validate_text(text)
                bundle={**value['bundle'],'text':text.strip()};kind='chat';history=value['messages'][-6:]
                live=bundle['reference'].get('live_dependency')
                if visual and live and self.app.extensions['preview_manager'].snapshot(uid,live['preview_id']).get('status')=='active':
                    # 附图提问刷新当前摘要，避免一直引用打开侧栏时的动作；密钥来源变化仍需重新确认。
                    fresh=self.builder.build(uid,auth,{'context_kind':'live','context_id':live['preview_id'],
                        'history_scope':bundle['reference']['history_scope'],'text':text})
                    if any(fresh['reference'][key]!=bundle['reference'][key] for key in ('settings_version','profile_version')):
                        raise APIError('AI_CONTEXT_CHANGED','资料或设置已改变，请重新打开AI助理',409)
                    bundle=fresh
                elif not self.builder.current(uid,auth,bundle['reference']):
                    raise APIError('AI_CONTEXT_CHANGED','记录或设置已改变，请重新打开AI助理',409)
            else:
                kind,bundle=self.report_bundle(uid,auth,data)
                history=[]
            if cid:
                bundle={**bundle,'open_chat':True}
            else:
                import re
                if re.search(r'\d+(?:\.\d+)?\s*(?:公斤|千克|kg|市斤|磅|lb|厘米|cm|岁)|体重|身高|年龄|热量|营养|饮食|蛋白',bundle['text'],re.I):
                    raise APIError('AI_EPHEMERAL_COACH_REQUIRED','请使用AI教练处理本次身体资料，描述和结果默认仅本次使用',422)
                validate_fitness_question(bundle['text'])
            frames=[]
            if visual:
                if self.app.extensions['ai_settings'].get(uid,auth)['model']!='deepseek-flash':
                    raise APIError('AI_VISION_MODEL_UNAVAILABLE','当前模型暂不支持看图，请在设置中选择快速解读',422)
                try:frames=validate_frames(data.get('frames'))
                except ValueError as error:raise APIError('AI_IMAGE_INVALID',str(error),422) from None
                info={'frame_count':len(frames),'source':'user_attached_at_question','frames':[{'source_ms':f['source_ms'],'sha256':f['sha256']} for f in frames]}
                bundle={**bundle,'facts':{**bundle['facts'],'visual_input':info,'no_original_video_or_camera_images_sent':False},
                        'reference':{**bundle['reference'],'visual_input':info},
                        'visual_scene_only':bool(VISUAL_QUESTION.search(bundle['text'])) and not bool(VISUAL_FORM_QUESTION.search(bundle['text'])),
                        'visual_form_review':bool(VISUAL_FORM_QUESTION.search(bundle['text']))}
                bundle['reference']['visual_scene_only']=bundle['visual_scene_only']
                bundle['reference']['visual_form_review']=bundle['visual_form_review']
                bundle['reference']['visual_chat']=bool(cid)
            if cid:bundle={**bundle,'visual_scene_only':False,'visual_form_review':False}
            if frames:bundle['reference']['facts_sha256']=hashlib.sha256(json_text(bundle['facts']).encode()).hexdigest()
            messages=messages_for(bundle)
            if frames:attach_frames(messages,frames)
            if history:
                # 历史是当前内存中的已校验文字，仅作对话衔接，不能取代本次证据。
                history_text=json_text({'previous_messages_are_untrusted_conversation':history})
                while len(history_text.encode())>32000 and history:
                    history=history[1:];history_text=json_text({'previous_messages_are_untrusted_conversation':history})
                if history:messages.insert(1,{'role':'user','content':history_text})
            row,created=self.usage.reserve(uid,auth,rid,digest,kind,bundle['reference'],messages,bundle['reference']['settings_version'])
            if not created:return self.snapshot(uid,auth,rid)
            self.answers[rid]={'uid':uid,'auth':auth,'result':None,'touched':time.monotonic()}
            try:self.jobs.put_nowait((uid,auth,rid,bundle,messages,cid))
            except queue.Full:
                self.usage.finish(uid,rid,error='AI_BUSY');return self.snapshot(uid,auth,rid)
            self._start_workers();return row

    def _worker(self):
        while True:
            try:item=self.jobs.get(timeout=.25)
            except queue.Empty:
                if self.closed:return
                continue
            try:self._execute(*item)
            except Exception as error:
                # 不输出原始异常/请求正文，凭据从不进入队列或普通日志。
                self.app.logger.error('AI任务收尾失败 request_id=%s type=%s',item[2],type(error).__name__)
                try:
                    with self.usage.db.transaction(False) as c:sent=self.usage._row(c,item[0],item[2])['started_at_ms'] is not None
                    self.usage.finish(item[0],item[2],error='AI_NETWORK_UNCERTAIN' if sent else 'AI_SERVICE_STOPPED',cost=None if sent else 0,uncertain=sent)
                except Exception:self.app.logger.error('AI费用状态待恢复 request_id=%s',item[2])
            finally:
                # 工作线程等待下一条请求前释放本次图片，不在对话或账本保留原图。
                item=None
                self.jobs.task_done()

    def _execute(self,uid,auth,rid,bundle,messages,cid):
        if self.closed:
            self.usage.finish(uid,rid,error='AI_SERVICE_STOPPED');return
        try:
            if not self.builder.current(uid,auth,bundle['reference']):raise APIError('AI_CONTEXT_CHANGED','依据发生变化',409)
            work=self.usage.begin(uid,rid)
        except (APIError,ConflictError):
            self.usage.finish(uid,rid,error='AI_CONTEXT_CHANGED');return
        except StorageError:
            self.usage.finish(uid,rid,error='AI_KEY_STORAGE_UNAVAILABLE',cost=0);return
        if work is None:return
        if self.usage.cancellation_requested(uid,rid):
            self.usage.finish(uid,rid,error='AI_SERVICE_STOPPED');return
        try:
            provider=self.app.extensions['ai_provider_factory']()
            def stream_summary(text):
                # 流式正文同样经过事实文字校验；不传原始JSON或尚未核对的建议卡。
                try:checked=(validate_chat_answer if bundle.get('open_chat') else validate_answer)({'summary':text,'explanations':[],'next_focus':[],'limitations':[]},bundle)['summary']
                except ValueError:return
                with self.lock:
                    cached=self.answers.get(rid)
                    if cached and not self.closed:cached.update(stream_text=checked,touched=time.monotonic())
            if cid and callable(getattr(provider,'generate_stream',None)):
                response=provider.generate_stream(work['key'],work['model'],messages,work['max_output_tokens'],on_summary=stream_summary)
            else:response=provider.generate(work['key'],work['model'],messages,work['max_output_tokens'])
        except ProviderError as error:
            self.usage.finish(uid,rid,error=error.code,usage=error.usage,cost=error.estimated_micro_cny,uncertain=error.uncertain_charge);return
        except Exception:
            self.usage.finish(uid,rid,error='AI_NETWORK_UNCERTAIN',uncertain=True);return
        finally:work.pop('key',None)
        usage=response['usage'];cost=response['estimated_micro_cny']
        try:result=(validate_chat_answer if bundle.get('open_chat') else validate_answer)(response['result'],bundle)
        except ValueError as error:
            # 校验器仅返回固定规则说明，保留排障原因，不记录回复正文、图片或密钥。
            self.app.logger.warning('AI结果校验未通过 request_id=%s reason=%s',rid,str(error))
            selected_ids=set(bundle['reference']['record_ids'])
            observed=[v for v in bundle['facts']['evidence'] if v.get('status')=='issue' and
                (v.get('record_id') in selected_ids or v.get('kind')=='live_snapshot' and bundle['facts'].get('live'))]
            if VISUAL_FORM_QUESTION.search(bundle['text']) and observed:
                # 保留校验失败事实，使用已有证据提供明确答复，不展示未通过的模型内容。
                observed.sort(key=lambda v:v.get('source')!='user_confirmed_demo_annotation')
                fallback={'summary':'根据当前记录，这个动作有需要调整的地方。下面列出已有依据和对应建议。',
                    'explanations':[{'evidence_id':v['id'],'explanation':'这是你确认的演示问题，可按对应建议调整。' if v.get('source')=='user_confirmed_demo_annotation' else '当前记录已标出这项姿态问题，可按对应建议调整。',
                        'adjustment':'先稳定姿态，再按记录中的建议调整。'} for v in observed[:3]],
                    'next_focus':[],'limitations':['本次回答依据已有记录，画面模型回复未通过核验。']}
                result=validate_answer(fallback,bundle)
                result['response_basis']='registered_evidence_fallback_after_invalid_provider_answer'
                result['provider_answer_rejected']=True
            else:
                self.usage.finish(uid,rid,error='AI_EVIDENCE_INVALID',usage=usage,cost=cost);return
        # 生成期间的注销、删除/修订、档案变更，不能使旧答案冒充当前结论。
        if self.closed or not self.builder.current(uid,auth,bundle['reference']):
            self.usage.finish(uid,rid,error='AI_CONTEXT_CHANGED',usage=usage,cost=cost);return
        with self.lock:
            row=self.usage.finish(uid,rid,result=result,usage=usage,cost=cost)
            if row['status']!='succeeded':return
            cached=self.answers.get(rid)
            if cached:cached.update(result=result,touched=time.monotonic())
            value=self.conversations.get(cid)
            if value and value['uid']==uid and value['auth']==auth:
                content={'summary':result['summary'],'explanations':[{k:v for k,v in item.items() if k in {'explanation','adjustment'}} for item in result['explanations']],
                         'next_focus':result['next_focus'],'limitations':result['limitations']}
                value['messages'].extend([{'role':'user','content':bundle['text']},{'role':'assistant','content':json_text(content)}])
                value['messages']=value['messages'][-20:];value['touched']=time.monotonic()

    def snapshot(self,uid,auth,rid):
        with self.lock:
            self._prune();row=self.usage.get(uid,auth,rid)
            if row['status'] in TERMINAL and row['status']!='succeeded':self.answers.pop(rid,None)
            current=self.builder.current(uid,auth,row['context']) if row['status'] in TERMINAL else None
            row['report_body_deleted']=row['kind'] in {'review','comparison'} and row['status']=='succeeded' and row['result'] is None
            cached=self.answers.get(rid)
            if cached and cached['uid']==uid and cached['auth']==auth and row['status']=='running' and cached.get('stream_text'):
                streaming_current=self.builder.current(uid,auth,row['context'])
                row['stream_text']=cached['stream_text'] if streaming_current else ''
                current=streaming_current
            if current and row['status']=='succeeded' and row['result'] is None and cached and cached['uid']==uid and cached['auth']==auth:
                row['result']=cached['result']
            row['context_current']=current
            row['live_context']=self.builder.live_context(uid,row['context']) if current else None
            if not current:row['result']=None
            elif row['result'] and row['kind'] in {'review','comparison','interpretation'}:row['result']=report_display(row['result'])
            row['conversation_content_expired']=row['status']=='succeeded' and row['kind'] in {'chat','interpretation'} and row['result'] is None
            return row

    def close(self,uid,auth,cid):
        with self.lock:
            self._conversation(uid,auth,cid);self.conversations.pop(cid,None)
        return {'closed':True}

    def revoke(self,user_id,auth_session_id=None):
        with self.lock:
            for mapping in (self.conversations,self.answers):
                for key,value in list(mapping.items()):
                    if value['uid']==user_id and (auth_session_id is None or value['auth']==auth_session_id):mapping.pop(key,None)
        # actor检查会在发送前及完成后生效；正在上游执行的请求仍需记录真实用量。

    def invalidate_source_cache(self,uid,ids):
        if not ids:return
        removed=set(ids)
        def affected(ref,auth):
            direct=set(ref.get('record_ids',[]))|{v['id'] for v in ref.get('dependencies',[])}
            return bool(direct&removed or (ref.get('live_dependency') or {}).get('source_record_id') in removed or ref.get('history_fingerprint') and not self.builder.current(uid,auth,ref))
        with self.lock:
            for cid,value in list(self.conversations.items()):
                if value['uid']==uid and affected(value['bundle']['reference'],value['auth']):self.conversations.pop(cid,None)
            own=[rid for rid,v in self.answers.items() if v['uid']==uid]
            if own:
                with self.usage.db.transaction(False) as c:
                    rows=c.execute(select(s.ai_requests.c.id,s.ai_requests.c.context_json).where(s.ai_requests.c.user_id==uid,s.ai_requests.c.id.in_(own))).mappings().all()
                for row in rows:
                    cached=self.answers.get(row['id'])
                    if cached and affected(json.loads(row['context_json']),cached['auth']):self.answers.pop(row['id'],None)

    def stop(self):
        self.closed=True
        with self.lock:self.conversations.clear();self.answers.clear()
        # 不等待网络阻塞视觉服务关闭；下次启动只恢复账本，绝不重发。


def register_ai_jobs(app):
    manager=AIJobs(app);app.extensions['ai_jobs']=manager
    manager.usage.sessions.delete_hooks.append(manager.usage.clear_source_records)
    app.extensions['auth_revoke_hooks'].append(manager.revoke);app.extensions['stop_hooks'].insert(0,manager.stop)

    @app.post('/api/v1/ai/conversations')
    def ai_conversation_create():
        return jsonify(data=manager.create(current_user.user_id,current_user.id,request.headers.get('Idempotency-Key'),body(CONTEXT_FIELDS))),201

    @app.route('/api/v1/ai/conversations/<cid>',methods=['GET','DELETE'])
    def ai_conversation_get(cid):
        handler=manager.close if request.method=='DELETE' else manager.conversation
        return jsonify(data=handler(current_user.user_id,current_user.id,uuid_id(cid)))

    @app.post('/api/v1/ai/conversations/<cid>/messages')
    def ai_message(cid):
        data=body({'text','expected_context_revision','consent','include_current_frame','frames'},max_bytes=2*1024*1024)
        return jsonify(data=manager.submit(current_user.user_id,current_user.id,request.headers.get('Idempotency-Key'),data,uuid_id(cid))),202

    @app.post('/api/v1/ai/report-options')
    def ai_report_options():
        return jsonify(data=manager.report_options(current_user.user_id,current_user.id,body(CONTEXT_FIELDS|{'kind'})))

    @app.route('/api/v1/ai/reports',methods=['GET','POST'])
    def ai_report():
        if request.method=='GET':return jsonify(data=manager.report_list(current_user.user_id,current_user.id,dict(request.args)))
        data=body(CONTEXT_FIELDS|{'kind','text','consent','expected_context'})
        return jsonify(data=manager.submit(current_user.user_id,current_user.id,request.headers.get('Idempotency-Key'),data)),202

    @app.get('/api/v1/ai/requests/<rid>')
    def ai_request(rid):return jsonify(data=manager.snapshot(current_user.user_id,current_user.id,uuid_id(rid)))

    @app.post('/api/v1/ai/requests/<rid>/cancel')
    def ai_cancel(rid):
        data=body({'expected_version'})
        return jsonify(data=manager.usage.cancel(current_user.user_id,current_user.id,uuid_id(rid),data.get('expected_version'))),202

    @app.route('/api/v1/ai/reports/<rid>',methods=['GET','DELETE'])
    def ai_saved_report(rid):
        rid=uuid_id(rid);uid=current_user.user_id;auth=current_user.id
        row=manager.usage.get(uid,auth,rid)
        if row['kind'] not in {'review','comparison'}:raise APIError('NOT_FOUND','报告不存在',404)
        if request.method=='GET':return jsonify(data=manager.snapshot(uid,auth,rid))
        data=body({'expected_version'});version=integer(data.get('expected_version'),'expected_version')
        with manager.lock,manager.usage.db.transaction() as c:
            manager.usage.sessions._actor(c,uid,auth);raw=manager.usage._row(c,uid,rid)
            if raw['status'] not in TERMINAL:raise ConflictError('请先停止进行中的报告')
            if raw['state_version']!=version:raise ConflictError('报告已更新，请重新读取')
            c.execute(update(s.ai_requests).where(s.ai_requests.c.id==rid).values(result_json=None,state_version=version+1))
            manager.answers.pop(rid,None)
        return jsonify(data={'deleted':True,'usage_ledger_retained':True})

    @app.post('/api/v1/admin/ai-requests/<rid>/reconcile')
    def ai_reconcile(rid):
        data=body({'expected_version','estimated_micro_cny','note'})
        return jsonify(data=manager.usage.reconcile(current_user.user_id,current_user.id,uuid_id(rid),data.get('expected_version'),data.get('estimated_micro_cny'),data.get('note')))
