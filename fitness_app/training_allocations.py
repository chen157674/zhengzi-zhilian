"""分析记录的明确分配与计量守恒，删除/修订在原事务内使关联失效。"""
import json,datetime as dt
from flask import request,jsonify
from flask_login import current_user
from sqlalchemy import select,update,delete,or_
from . import db_schema as s
from .auth import APIError,body
from .database import ConflictError,idempotent,json_text
from .records import integer,uuid_id
from .training_plans import date,invalid,ZONE

def invalidate_records(c,uid,record_ids):
    rows=c.execute(select(s.training_record_allocations).where(s.training_record_allocations.c.user_id==uid,s.training_record_allocations.c.record_id.in_(record_ids),s.training_record_allocations.c.valid==1)).mappings().all()
    if not rows:return
    ids={x['workout_id'] for x in rows}
    c.execute(update(s.training_record_allocations).where(s.training_record_allocations.c.id.in_([x['id'] for x in rows])).values(valid=0))
    # 同一项存在多个来源时，只撤回实际受影响的组；权威可用量由读取时重算。
    for wid in ids:
        c.execute(update(s.training_workouts).where(s.training_workouts.c.id==wid,s.training_workouts.c.user_id==uid).values(state_version=s.training_workouts.c.state_version+1))

class TrainingAllocations:
    def __init__(self,app):self.app=app;self.workouts=app.extensions['training_workouts'];self.plans=app.extensions['training_plans'];self.db=self.plans.db;self.sessions=app.extensions['session_store']
    def _record(self,c,uid,rid):
        row=self.sessions._session(c,uid,uuid_id(rid));record=self.sessions.snapshot(uid,rid,True)
        if record.get('confirmed_demo_findings'):raise APIError('RECORD_NOT_AVAILABLE','人工演示标注不能用于自动计划完成量',422)
        if row['source']=='demo' or row['status'] not in {'completed','ended_early'} or record.get('included_in_statistics') is False:raise APIError('RECORD_NOT_AVAILABLE','请采用已结束并有效保存的本人记录',422)
        writer=self.app.extensions['persistence'];current=writer.snapshot(uid,rid)
        if current['save_state']!='clean':raise APIError('RECORD_NOT_AVAILABLE','记录保存尚未确认',409)
        if record['source']=='video':
            jobs=c.execute(select(s.jobs.c.status).where(s.jobs.c.session_id==rid)).scalars().all();revisions=c.execute(select(s.video_revisions.c.status).where(s.video_revisions.c.session_id==rid)).scalars().all()
            if record['status']!='completed' or any(x!='completed' for x in [*jobs,*revisions]):raise APIError('RECORD_NOT_AVAILABLE','上传分析须完整完成后才能采用',422)
        return record
    def _item(self,row,iid):
        item=next((x for x in json.loads(row['target_snapshot_json'])['items'] if x['id']==iid),None)
        if not item:raise APIError('NOT_FOUND','执行动作不存在',404)
        return item
    def candidates(self,uid,auth,wid,data):
        with self.db.transaction(False) as c:
            workout=self.workouts.row(c,uid,auth,wid);item=self._item(workout,uuid_id(data.get('item_id')))
        end=date(data.get('date_to') or self.plans.today().isoformat());start=date(data.get('date_from') or (end-dt.timedelta(days=90)).isoformat())
        if not 0<=(end-start).days<=90:invalid('date_range')
        page=integer(data.get('page',1),'page',1)
        records=self.sessions.list_records(uid,page=page,page_size=20,action_id=item['action_id'],date_from=start.isoformat(),date_to=end.isoformat(),search=data.get('query'))
        result=[]
        for summary in records['records']:
            with self.app.extensions['persistence'].deletion_guard(uid,[summary['session_id']]),self.db.transaction(False) as c:
                try:record=self._record(c,uid,summary['session_id'])
                except APIError:continue
                reasons=[]
                if item.get('variant') and record['config'].get('variant')!=item['variant']:reasons.append('动作变式无法确认或不一致')
                unit='ms' if item['target_duration_ms'] is not None else 'reps'
                used=c.execute(select(s.training_record_allocations).where(s.training_record_allocations.c.user_id==uid,s.training_record_allocations.c.valid==1,or_(s.training_record_allocations.c.record_id==record['session_id'],s.training_record_allocations.c.source_sha256==record['source_sha256'] if record['source_sha256'] else False))).mappings().all()
                used_cycles={n for link in used if link['record_id']==record['session_id'] for allocation in json.loads(link['allocations_json']) for n in allocation.get('cycle_ids',[])}
                used_ranges=[(span['start_ms'],span['end_ms']) for link in used for allocation in json.loads(link['allocations_json']) for span in allocation.get('cycle_ranges',[])]
                events=[e for e in record['events'] if e['event_seq'] not in used_cycles and not (record['source_sha256'] and any(e['start_ms']<right and left<e['end_ms'] for left,right in used_ranges))]
                hold_ranges=[{'start_ms':x['start_ms'],'end_ms':x['end_ms']} for x in record['intervals'] if x['kind']=='hold']
                for link in used:
                    for allocation in json.loads(link['allocations_json']):
                        if 'start_ms' not in allocation:continue
                        left,right=allocation['start_ms'],allocation['end_ms'];remaining=[]
                        for span in hold_ranges:
                            a,b=span['start_ms'],span['end_ms']
                            if left>=b or right<=a:remaining.append(span)
                            else:
                                if a<left:remaining.append({'start_ms':a,'end_ms':left})
                                if right<b:remaining.append({'start_ms':right,'end_ms':b})
                        hold_ranges=remaining
                available=sum(x['end_ms']-x['start_ms'] for x in hold_ranges) if unit=='ms' else (min(sum(e['side']=='left' for e in events),sum(e['side']=='right' for e in events)) if record['unit']=='per_side_reps' else len(events)) if record['counting_available'] else 0
                for link in used:
                    linked_date=c.execute(select(s.training_plan_sessions.c.scheduled_date).join(s.training_workouts,s.training_workouts.c.plan_session_id==s.training_plan_sessions.c.id).where(s.training_workouts.c.id==link['workout_id'])).scalar()
                    if linked_date!=json.loads(workout['target_snapshot_json'])['date']:
                        reasons.append('同源记录已用于其他计划日期');break
                if not record['counting_available'] and unit=='reps':reasons.append('该记录不能自动计量，可作为方法参考并手动记组')
                performed_date=dt.datetime.fromtimestamp((record['started_at_ms'] or record['created_at_ms'])/1000,ZONE).date().isoformat()
                result.append({'record_id':record['session_id'],'state_version':record['state_version'],'action_id':record['action_id'],'available_quantity':available,'unit':unit,'count_unit':record['unit'],'performed_date':performed_date,'source':record['source'],'reference_only':not record['counting_available'] and unit=='reps','compatible':not reasons and available>0,'reasons':reasons if available else reasons+['没有尚未采用的有效完成量'],'cycle_ids':[x['event_seq'] for x in events],'cycles':[{'id':e['event_seq'],'side':e['side'],'start_ms':e['start_ms'],'end_ms':e['end_ms']} for e in events],'hold_ranges':hold_ranges})
        return {'candidates':result,'page':records['page'],'has_more':records['page']*records['page_size']<records['total'],'candidate_limit':records['page_size']}

    def save(self,uid,auth,wid,data,key):
        key=uuid_id(key);version=integer(data.get('expected_version'),'expected_version');iid=uuid_id(data.get('item_id'));rid=uuid_id(data.get('record_id'));record_version=integer(data.get('record_expected_version'),'record_expected_version')
        if data.get('same_trainee') is not True:invalid('same_trainee','请确认本人训练记录')
        allocations=data.get('allocations')
        reference_only=data.get('reference_only',False)
        if type(reference_only)is not bool:invalid('reference_only')
        if not isinstance(allocations,list) or (not reference_only and not 1<=len(allocations)<=10) or (reference_only and allocations):invalid('allocations')
        with self.app.extensions['persistence'].deletion_guard(uid,[rid]),self.db.transaction() as c:
            row=self.workouts.row(c,uid,auth,wid)
            def write():
                if row['state_version']!=version:raise ConflictError('执行已变化')
                if row['status'] not in {'running','paused'}:raise APIError('INVALID_STATE','请先开始执行，结束后先重开',409)
                item=self._item(row,iid);record=self._record(c,uid,rid)
                if record['state_version']!=record_version:raise ConflictError('分析记录已修订')
                if record['action_id']!=item['action_id']:raise APIError('RECORD_NOT_COMPATIBLE','动作不同',422)
                if item.get('variant') and record['config'].get('variant')!=item['variant']:raise APIError('RECORD_NOT_COMPATIBLE','动作变式无法确认',422)
                session=c.execute(select(s.training_plan_sessions).where(s.training_plan_sessions.c.id==row['plan_session_id'])).mappings().one()
                performed=dt.datetime.fromtimestamp((record['started_at_ms'] or record['created_at_ms'])/1000,ZONE).date().isoformat()
                if performed!=session['scheduled_date']:
                    if data.get('performed_date')!=session['scheduled_date'] or data.get('late_entry_confirmed') is not True:raise APIError('RECORD_DATE_MISMATCH','记录日期与计划不同；补记须明确原计划日',422)
                used=list(c.execute(select(s.training_record_allocations).where(s.training_record_allocations.c.user_id==uid,s.training_record_allocations.c.valid==1,or_(s.training_record_allocations.c.record_id==rid,s.training_record_allocations.c.source_sha256==record['source_sha256'] if record['source_sha256'] else False))).mappings())
                for link in used:
                    source_date=c.execute(select(s.training_plan_sessions.c.scheduled_date).join(s.training_workouts,s.training_workouts.c.plan_session_id==s.training_plan_sessions.c.id).where(s.training_workouts.c.id==link['workout_id'])).scalar()
                    if source_date!=session['scheduled_date']:raise APIError('RECORD_ALREADY_ALLOCATED','同源训练不能分配到不同计划日期',409)
                if reference_only:
                    if any(x['workout_id']==wid and x['item_id']==iid and x['record_id']==rid and x['unit']=='reference' for x in used):raise APIError('RECORD_ALREADY_ALLOCATED','该参考记录已关联',409)
                    lid=s.new_id();c.execute(s.training_record_allocations.insert().values(id=lid,user_id=uid,workout_id=wid,item_id=iid,record_id=rid,record_version=record_version,allocations_json='[]',source_sha256=record['source_sha256'],unit='reference',allocated_quantity=0))
                    c.execute(update(s.training_workouts).where(s.training_workouts.c.id==wid).values(state_version=version+1))
                    return {'id':wid,'version':version+1,'link_id':lid}
                unit='ms' if item['target_duration_ms'] is not None else 'reps';seen_sets=set();seen_cycles=set();ranges=[];clean=[];total=0
                existing_cycles={cycle for link in used for a in json.loads(link['allocations_json']) for cycle in a.get('cycle_ids',[])}
                existing_ranges=[(a['start_ms'],a['end_ms']) for link in used for a in json.loads(link['allocations_json']) if 'start_ms'in a]
                used_cycle_ranges=[(r['start_ms'],r['end_ms']) for link in used for a in json.loads(link['allocations_json']) for r in a.get('cycle_ranges',[])]
                if unit=='reps' and (not record['counting_available'] or record['unit']=='seconds'):raise APIError('RECORD_NOT_COMPATIBLE','记录不提供该完成量',422)
                for allocation in allocations:
                    if not isinstance(allocation,dict):invalid('allocations')
                    index=integer(allocation.get('set_index'),'set_index',1,item['target_sets'])
                    if index in seen_sets:invalid('set_index','同组不可重复分配')
                    seen_sets.add(index)
                    existing=c.execute(select(s.training_workout_sets).where(s.training_workout_sets.c.workout_id==wid,s.training_workout_sets.c.item_id==iid,s.training_workout_sets.c.set_index==index)).first()
                    if existing:raise APIError('SET_ALREADY_RECORDED','请先撤销该组原记录或解除关联',409)
                    if unit=='reps':
                        if set(allocation)!={'set_index','cycle_ids'}:invalid('cycle_ids')
                        ids=allocation['cycle_ids']
                        if not isinstance(ids,list) or not 1<=len(ids)<=100 or any(type(x)is not int for x in ids) or len(ids)!=len(set(ids)):invalid('cycle_ids')
                        available={x['event_seq'] for x in record['events']}
                        if not set(ids)<=available:invalid('cycle_ids','周期不在保存记录中')
                        if seen_cycles.intersection(ids) or existing_cycles.intersection(ids):raise APIError('RECORD_ALREADY_ALLOCATED','同源周期已被采用',409)
                        selected_events=[e for e in record['events'] if e['event_seq'] in ids]
                        if record['source_sha256'] and any(e['start_ms']<end and start<e['end_ms'] for e in selected_events for start,end in used_cycle_ranges):raise APIError('RECORD_ALREADY_ALLOCATED','同源时间范围已在其他修订采用',409)
                        seen_cycles.update(ids)
                        if record['unit']=='per_side_reps':
                            selected=[x for x in record['events'] if x['event_seq'] in ids];left_count=sum(x['side']=='left' for x in selected);right_count=sum(x['side']=='right' for x in selected)
                            if not left_count or left_count!=right_count or left_count+right_count!=len(ids):invalid('cycle_ids','每侧次数动作须明确选择左右等量完整周期')
                            quantity=left_count
                        else:quantity=len(ids)
                        entry={'set_index':index,'cycle_ids':ids,'cycle_ranges':[{'start_ms':e['start_ms'],'end_ms':e['end_ms']} for e in selected_events]}
                    else:
                        if set(allocation)!={'set_index','start_ms','end_ms'}:invalid('range')
                        left=integer(allocation['start_ms'],'start_ms');right=integer(allocation['end_ms'],'end_ms',left+1,left+600000)
                        if not any(x['kind']=='hold' and x['start_ms']<=left<right<=x['end_ms'] for x in record['intervals']):invalid('range','时间段不在有效保持区间')
                        if any(left<b and a<right for a,b in existing_ranges+ranges):raise APIError('RECORD_ALREADY_ALLOCATED','同源区间已被采用',409)
                        ranges.append((left,right));quantity=right-left;entry={'set_index':index,'start_ms':left,'end_ms':right}
                    total+=quantity;clean.append(entry)
                    c.execute(s.training_workout_sets.insert().values(id=s.new_id(),user_id=uid,workout_id=wid,item_id=iid,set_index=index,actual_reps=quantity if unit=='reps' else None,actual_duration_ms=quantity if unit=='ms' else None,status='done',source='record',saved_at_ms=self.plans.clock()))
                lid=s.new_id();c.execute(s.training_record_allocations.insert().values(id=lid,user_id=uid,workout_id=wid,item_id=iid,record_id=rid,record_version=record_version,allocations_json=json_text(clean,list),source_sha256=record['source_sha256'],unit=unit,allocated_quantity=total))
                c.execute(update(s.training_workouts).where(s.training_workouts.c.id==wid).values(state_version=version+1))
                return {'id':wid,'version':version+1,'link_id':lid}
            idempotent(c,uid,key,data,'training.record.link',write,wid)
        return self.workouts.get(uid,auth,wid)
    def remove(self,uid,auth,wid,lid,data,key):
        version=integer(data.get('expected_version'),'expected_version');key=uuid_id(key);lid=uuid_id(lid)
        with self.db.transaction() as c:
            row=self.workouts.row(c,uid,auth,wid)
            def write():
                if row['state_version']!=version:raise ConflictError('执行已更新')
                if row['status'] not in {'running','paused'}:raise APIError('INVALID_STATE','请先重新打开执行',409)
                link=c.execute(select(s.training_record_allocations).where(s.training_record_allocations.c.id==lid,s.training_record_allocations.c.workout_id==wid,s.training_record_allocations.c.user_id==uid)).mappings().first()
                if not link:raise APIError('NOT_FOUND','记录关联不存在',404)
                indices=[x['set_index'] for x in json.loads(link['allocations_json'])]
                c.execute(delete(s.training_workout_sets).where(s.training_workout_sets.c.workout_id==wid,s.training_workout_sets.c.item_id==link['item_id'],s.training_workout_sets.c.set_index.in_(indices),s.training_workout_sets.c.source=='record'))
                c.execute(delete(s.training_record_allocations).where(s.training_record_allocations.c.id==lid))
                c.execute(update(s.training_workouts).where(s.training_workouts.c.id==wid).values(state_version=version+1));return {'id':wid,'version':version+1}
            idempotent(c,uid,key,data,'training.record.unlink',write,wid+':'+lid)
        return self.workouts.get(uid,auth,wid)

def register_allocations(app):
    service=TrainingAllocations(app);app.extensions['training_allocations']=service
    app.extensions['session_store'].delete_hooks.append(invalidate_records)
    @app.post('/api/v1/training/workouts/<wid>/record-candidates')
    def record_candidates(wid):return jsonify(data=service.candidates(current_user.user_id,current_user.id,wid,body({'item_id','query','date_from','date_to','page'})))
    @app.post('/api/v1/training/workouts/<wid>/record-links')
    def record_link(wid):return jsonify(data=service.save(current_user.user_id,current_user.id,wid,body({'expected_version','item_id','record_id','record_expected_version','allocations','same_trainee','performed_date','late_entry_confirmed','reference_only'}),request.headers.get('Idempotency-Key')))
    @app.delete('/api/v1/training/workouts/<wid>/record-links/<lid>')
    def unlink_record(wid,lid):return jsonify(data=service.remove(current_user.user_id,current_user.id,wid,lid,body({'expected_version'}),request.headers.get('Idempotency-Key')))
