import {api,confirmDialog} from './app.js';

const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
export class VideoDeletion {
 constructor({run,beforeDelete,complete}){
  this.run=run;this.beforeDelete=beforeDelete;this.complete=complete;this.parent=null;this.pending=null;this.blocked=false;this.retry=false;this.disposed=false;
  this.key='fitness-video-delete-pending:'+document.body.dataset.userId;
  this.panel=document.getElementById('video-delete-panel');this.button=document.getElementById('video-delete');this.notice=document.getElementById('video-delete-recovery');this.message=document.getElementById('video-delete-message');this.check=document.getElementById('video-delete-check');
  try{const raw=sessionStorage.getItem(this.key);if(raw){const value=JSON.parse(raw);if(!uuid.test(value.id)||!uuid.test(value.analysis_id)||!Number.isSafeInteger(value.body?.expected_version)||value.body.expected_version<0||!/^[0-9a-f]{64}$/.test(value.body.expected_inventory)||value.body.confirm!==true)throw new Error('原删除标识无效，请先核对最近的视频分析。');this.pending=value;this.text('原删除结果待核对；不会自动重新删除。');}}catch(e){this.storageError=true;this.text(e.message||'请重新读取待删除记录。');}
  this.button.onclick=()=>this.run(()=>this.begin());this.check.onclick=()=>this.run(()=>this.retry?this.submit():this.verify());
  window.addEventListener('pagehide',()=>{this.disposed=true;});
  window.addEventListener('fitness-auth-revoked',()=>{this.disposed=true;this.pending=null;try{sessionStorage.removeItem(this.key);}catch{}this.notice.hidden=true;});
  const query=new URLSearchParams(location.search),operation=query.get('deleted');
  if(operation&&uuid.test(operation))queueMicrotask(()=>this.completedReceipt(operation));
  else if(query.get('analysis-unavailable')==='1'&&!this.pending)this.text('请从本人的最近记录重新打开视频分析。');
  this.update(null,false);
 }
 text(value){this.notice.hidden=false;this.message.textContent=value;this.check.hidden=!this.pending;}
 update(parent,blocked){this.parent=parent;this.blocked=blocked;this.panel.hidden=!parent;this.button.disabled=blocked||this.storageError||Boolean(this.pending)||!parent?.can_delete;this.check.disabled=blocked;}
 remember(value){sessionStorage.setItem(this.key,JSON.stringify(value));this.pending=value;this.retry=false;this.check.textContent='核对原删除';this.text('正在删除，请查看原请求进度。');}
 forget(){this.pending=null;this.retry=false;try{sessionStorage.removeItem(this.key);}catch{}this.check.hidden=true;}
 async begin(){
  if(!this.parent||this.pending)return;
  const id=this.parent.analysis_id,scope=await api('/api/v1/video-analyses/'+id+'/delete-options');if(this.disposed)return;
  const yes=await confirmDialog({title:'删除本次视频分析？',message:`将删除 ${scope.segment_count} 个片段、${scope.revision_count} 次修订和 ${scope.record_count} 条关联记录（含旧版与未发布记录），并清理关联备注、骨架、运动摘要及依赖的报告正文。已发生AI用量和设备上的原视频保留。`,confirmLabel:'删除分析及关联记录',danger:true});
  if(!yes||this.disposed)return;
  this.remember({id:crypto.randomUUID(),analysis_id:id,body:{expected_version:scope.expected_version,expected_inventory:scope.inventory_version,confirm:true}});
  await this.submit();
 }
 leave(id,unavailable){this.disposed=true;this.complete(id,unavailable);}
 async submit(){
  const original=this.pending;if(!original)return;
  // 已确认离开这次分析后，先释放本页未使用的上传副本；设备原文件保持本地。
  await this.beforeDelete();if(this.disposed)return;
  try{
   const result=await api('/api/v1/video-analyses/'+original.analysis_id,{method:'DELETE',commandId:original.id,body:original.body});
   if(this.disposed||this.pending?.id!==original.id)return;
   if(!result.deleted||result.id!==original.analysis_id)throw new Error('删除回执与原分析不一致，请核对原请求。');
   this.forget();this.text('删除已确认，正在返回视频分析。');this.leave(original.id,false);
  }catch(e){
   if(this.disposed)return;
   if(e.unknown){this.retry=false;this.check.textContent='核对原删除';this.text('删除结果待核对；点击“核对原删除”不会发起另一批删除。');}
   else if(e.status===404){this.forget();this.leave(null,true);}
   else{this.forget();this.text('删除未完成；内容或状态可能变化，请重新核对删除范围。');}
   throw e;
  }
 }
 async verify(){
  const original=this.pending;if(!original)return;
  try{
   const row=await api('/api/v1/operations/'+original.id);if(this.disposed||this.pending?.id!==original.id)return;
   if(row.status!=='succeeded'||!row.result?.deleted||row.result.id!==original.analysis_id)throw new Error('请稍后查看原删除结果。');
   this.forget();this.leave(original.id,false);
  }catch(e){
   if(this.disposed||this.pending?.id!==original.id)return;
   if(e.status!==404)throw e;
   try{await api('/api/v1/video-analyses/'+original.analysis_id);}catch(read){if(read.status===404){this.forget();this.leave(null,true);return;}throw read;}
   if(this.disposed)return;this.retry=true;this.check.textContent='按原请求重试删除';this.text('请核对待删除内容后按原请求重试。');
  }
 }
 async completedReceipt(id){
  try{const row=await api('/api/v1/operations/'+id);if(this.disposed)return;if(row.status!=='succeeded'||!row.result?.deleted)throw new Error('请从最近记录查看删除结果。');if(this.pending?.id===id)this.forget();this.text('已删除本次分析及关联记录，设备上的原视频保留。');}
  catch(e){if(!this.disposed)this.text(e.message||'请查看删除结果。');}
 }
}
