import {api,confirmDialog,showMessage} from './app.js';
import {el,terminal,dateFormat,duration,quantity,recordStatus,issueNames,privateError} from './record_common.js';
import {historyVideo} from './record_video.js';
import {renderRecordRetest} from './retest_ui.js';
import {mountSkeletonReplay} from './skeleton_replay.js';
import {renderStability} from './stability_ui.js';
import {openReportExport} from './report_export_ui.js';
import {openAIReport} from './ai_report_ui.js';
import {feedbackSummary} from './feedback_summary.js';

const root=document.querySelector('.record-detail'),sid=root.dataset.sessionId,back=document.querySelector('#record-back');
const errorBox=document.querySelector('#detail-error'),reload=document.querySelector('#detail-reload');
const retry=document.querySelector('#save-retry'),discard=document.querySelector('#save-discard');
let data=null,busy=false,timer=null,disposed=false;
let mediaPlayer=null,mediaMounted=false;
try{const candidate=new URL(new URLSearchParams(location.search).get('returnTo')||'/records?view=list',location.origin);if(candidate.origin===location.origin&&candidate.pathname==='/records')back.href=candidate.pathname+candidate.search}catch{/* 无效返回地址使用本人列表。 */}
function timeRange(start,end){return `${duration(start)}–${duration(end)}`}
function qualityText(item){const label=issueNames[item.code||item.item_code]||'质量判项';return label+'：'+(item.status==='issue'?'发现问题':item.status==='not_observed'?'未发现此问题':'无法评估')+(item.reason?'（'+item.reason+'）':'')}
document.querySelector('#record-export').onclick=()=>openReportExport([sid]);
document.querySelector('#record-ai-review').onclick=()=>openAIReport({record_ids:[sid]});
function render(){
  document.querySelector('#record-ai-review').hidden=document.querySelector('#record-export').hidden=data.source==='demo';
  document.querySelector('#record-ai-review').disabled=!terminal.has(data.status)||data.save_state!=='clean'||data.started_at_ms===null||data.source==='demo'||data.included_in_statistics===false;
  document.querySelector('#record-export').disabled=!terminal.has(data.status)||data.save_state!=='clean'||data.started_at_ms===null||data.source==='demo'||data.included_in_statistics===false;
  renderRecordRetest(data);
  const notice=document.querySelector('#revision-notice');notice.hidden=data.included_in_statistics!==false||data.source==='demo';notice.replaceChildren();if(!notice.hidden){notice.append(el('span',data.superseded_by?'这是历史结果。':'重新分析尚未完成。'));if(data.superseded_by){const a=el('a','查看当前结果 →','text-link');a.href='/records/'+encodeURIComponent(data.superseded_by);notice.append(a);}}
  document.querySelector('#detail-title').textContent=data.action_name;
  document.querySelector('#detail-meta').textContent=`${data.source==='camera'?'摄像头':data.source==='demo'?'内置演示 · 不计入个人训练统计':'上传视频'} · ${recordStatus(data)} · ${data.started_at_ms?dateFormat.format(data.started_at_ms):'未开始'}`;
  document.querySelector('#record-new').href=data.source==='camera'?'/realtime':data.source==='demo'?'/video?demo=1':'/video?action='+encodeURIComponent(data.action_id);
  if(data.config?.video_analysis_id&&!new URLSearchParams(location.search).has('returnTo')){back.href='/video/analyses/'+encodeURIComponent(data.config.video_analysis_id);for(const node of back.childNodes)if(node.nodeType===Node.TEXT_NODE)node.textContent='返回视频分析';}
  document.querySelector('#detail-summary').hidden=false;
  document.querySelector('#detail-count').textContent=quantity(data);
  document.querySelector('#detail-side').textContent=(data.unit==='per_side_reps'||data.action_id==='high_knees'?`左侧 ${data.left_count} 次 · 右侧 ${data.right_count} 次`:'')+(data.count_accuracy_status==='evidence_insufficient'?(data.unit==='seconds'?'':''):'');
  document.querySelector('#detail-active').textContent=duration(data.active_ms);
  document.querySelector('#detail-coverage').textContent=data.unit==='seconds'?'按时段记录':`${data.evaluable_count} / ${data.total_count} 次`;
  document.querySelector('#detail-coverage-note').textContent=data.unit==='seconds'?'支撑计时与质量结论分别统计':`${data.unassessable_count} 次没有可评估结论；不计为正确`;
  const quality=document.querySelector('#detail-quality');quality.replaceChildren();
  const summary=feedbackSummary(data),verdict=el('div',undefined,'record-verdict');verdict.dataset.state=summary.state;
  verdict.append(el('span',summary.state==='clear'?'✓':summary.state==='issue'?'!':'·','verdict-mark'),el('strong',summary.title));quality.append(verdict);
  for(const item of summary.issues){
    const row=el('article',undefined,'feedback-row'),heading=el('div');
    heading.append(el('strong',item.message||issueNames[item.code]||'姿态反馈'),el('p',timeRange(item.start_ms,item.end_ms),'muted'));
    row.append(heading,el('p',item.suggestion||(item.status==='unassessable'?'此时段证据不足，无法判断。':'')));
    if(data.source!=='camera'){const seek=el('button','定位片段 →','text-link');seek.type='button';seek.onclick=()=>mediaPlayer?.seek(item.start_ms);row.append(seek);}quality.append(row);
  }
  if(summary.checks.length){const checks=el('details',undefined,'record-checks');checks.append(el('summary','检查明细'));for(const check of summary.checks){const p=el('p');p.append(el('strong',check.title),el('span',check.status==='issue'?'需要调整':check.status==='clear'?'正常':'暂无结论'));checks.append(p);}quality.append(checks);}
  document.querySelector('#quality-panel').hidden=false;
  const media=document.querySelector('#record-media');media.hidden=data.source==='camera';
  if(!mediaMounted){mediaMounted=true;media.replaceChildren();if(data.source==='camera')media.hidden=true;else{media.append(el('h2','原片回看'));mediaPlayer=historyVideo(media,sid,data.source==='demo',data);}}
  mediaPlayer?.updateRecord?.(data);
  mountSkeletonReplay(data,mediaPlayer||{});
  renderStability(data);
  document.querySelector('#detail-action').textContent=data.action_name;
  document.querySelector('#detail-source').textContent=data.source==='camera'?'实时分析':data.source==='demo'?'内置演示':'视频分析';
  document.querySelector('#record-delete').disabled=busy||!terminal.has(data.status)||data.save_state!=='clean';
  const groupRows=document.querySelector('#group-rows');groupRows.replaceChildren();
  for(const group of data.groups){const row=el('tr');const goal=group.target_value===null?'自由训练':`${group.target_value} ${group.target_unit==='seconds'?'秒':group.target_unit==='per_side_reps'?'次／侧':'次'}`;const amount=data.unit==='seconds'?`${(group.hold_ms/1000).toFixed(1)} 秒`:data.unit==='per_side_reps'?`左 ${group.left_count} · 右 ${group.right_count}`:data.action_id==='high_knees'?`${group.total_count} 次（左 ${group.left_count} · 右 ${group.right_count}）`:`${group.total_count} 次`;row.append(el('td',String(group.set_index)),el('td',goal),el('td',amount),el('td',({pending:'未开始',active:'进行中',completed:'已完成',ended_early:'提前结束'})[group.status]||group.status));groupRows.append(row)}
  document.querySelector('#record-groups').hidden=data.mode!=='target';
  const list=document.querySelector('#detail-event-list');list.replaceChildren();
  if(data.unit==='seconds'){
    for(const interval of data.intervals){const item=el('article',undefined,'event-row');const kind=interval.kind==='hold'?'确认支撑':interval.kind==='issue'?'发现问题':'无法评估';item.append(el('strong',`${timeRange(interval.start_ms,interval.end_ms)} · ${kind}`));if(interval.item_code)item.append(el('span',issueNames[interval.item_code]||'质量判项'));if(interval.reason)item.append(el('p',interval.reason,'muted'));list.append(item)}
  }else{
    for(const event of data.events){const item=el('article',undefined,'event-row');item.append(el('strong',`第 ${event.event_seq} 次 · ${timeRange(event.start_ms,event.end_ms)}${event.side==='left'?' · 左侧':event.side==='right'?' · 右侧':''}`));if(!event.assessments.length)item.append(el('p','质量无法评估：暂无检测结果。','muted'));else for(const assessment of event.assessments)item.append(el('p',qualityText(assessment),'muted'));list.append(item)}
  }
  if(!list.childElementCount)list.append(el('p','没有已保存的动作或时段明细。','muted'));
  if(mediaPlayer){
    const ranges=data.unit==='seconds'?data.intervals.filter(item=>item.kind==='issue'):data.events.filter(item=>item.assessments.some(value=>value.status==='issue'));
    for(const item of ranges){const button=el('button',`回看问题 ${timeRange(item.start_ms,item.end_ms)}`,'video-event');button.type='button';button.onclick=()=>mediaPlayer.seek(item.start_ms);list.append(button)}
  }
  document.querySelector('#detail-event-count').textContent=`（${data.unit==='seconds'?data.intervals.length:data.events.length} 条）`;
  document.querySelector('#record-events').hidden=data.unit==='seconds'?!data.intervals.length:!data.events.length;
  const failed=data.save_state==='save_failed',banner=document.querySelector('#save-banner');banner.hidden=!failed;
  if(failed){document.querySelector('#save-description').textContent=`最后确认保存：${dateFormat.format(data.last_saved_at_ms)}。`+(data.pending?.event_seq?`第 ${data.pending.event_seq} 次动作尚未确认写入。`:'其后未保存范围尚未确认。')+' 重试只提交原批次，不新建分析。';document.querySelector('#save-title').textContent=data.unsaved_discarded?'已放弃未保存部分，保存尚未完成':'保存失败，分析已暂停'}
  retry.disabled=discard.disabled=busy;
  discard.hidden=Boolean(data.unsaved_discarded);
  const saved=document.querySelector('#detail-save-status');saved.hidden=failed||!data.analysis_frozen;
  if(!failed)saved.textContent=`已保存至 ${dateFormat.format(data.last_saved_at_ms)}。`+(data.analysis_frozen?'分析仍暂停，须在分析页明确继续。':'');
}
async function load(){
  if(disposed)return;
  clearTimeout(timer);
  try{const value=await api('/api/v1/records/'+sid);if(disposed)return;data=value;render();errorBox.hidden=reload.hidden=true}
  catch(error){if(disposed||privateError(error))return;errorBox.textContent=error.message+(data?'；以下保留上次已读取摘要。':'');errorBox.hidden=reload.hidden=false}
  finally{if(!disposed&&data&&(!terminal.has(data.status)||data.save_state==='save_failed'))timer=setTimeout(load,2000)}
}
async function recovery(command,commandId=crypto.randomUUID()){
  const body={command,expected_version:data.state_version};if(command==='discard_unsaved'){body.confirm=true;body.last_committed_version=data.state_version}
  return await api(`/api/v1/sessions/${sid}/commands`,{method:'POST',commandId,body});
}
retry.onclick=async()=>{
  if(busy)return;busy=true;render();clearTimeout(timer);
  try{const result=await recovery('retry_save');if(result.save_state==='save_failed')showMessage('原批次仍未保存，请检查存储后重试。');await load()}
  catch(error){if(!privateError(error)){await load();showMessage(error.message)}}finally{busy=false;if(data)render()}
};
discard.onclick=async()=>{
  if(busy)return;busy=true;render();clearTimeout(timer);
  const commandId=crypto.randomUUID(),body={command:'discard_unsaved',expected_version:data.state_version,last_committed_version:data.state_version,confirm:true};let acknowledged=null;
  try{
    const confirmed=await confirmDialog({title:'放弃未保存部分',message:`已保存至 ${dateFormat.format(data.last_saved_at_ms)}。其后尚未确认写入的动作、时间和事件会丢失，无法恢复；已有摘要保留。`,confirmLabel:'确认放弃未保存部分',danger:true,onConfirm:async()=>{
      acknowledged=await api(`/api/v1/sessions/${sid}/commands`,{method:'POST',commandId,body});
      if(!acknowledged.unsaved_discarded)throw new Error('未取得放弃确认，请核对原会话状态。');return true;
    }});
    if(confirmed){const destination=new URL(back.href);destination.searchParams.set('discarded',acknowledged.terminal_persisted===false?'pending':'confirmed');location.assign(destination.href);return}
  }catch(error){if(!privateError(error))showMessage(error.message)}finally{busy=false;if(data)render();if(!acknowledged)load()}
};
reload.onclick=load;
window.addEventListener('pagehide',()=>{disposed=true;clearTimeout(timer)});
load();

document.querySelector('#record-delete').onclick=async()=>{if(busy||!data)return;const key=crypto.randomUUID(),payload={ids:[sid],expected_versions:{[sid]:data.state_version}};let uncertain=false;
await confirmDialog({title:'删除这条训练记录？',message:'删除后无法恢复；不会删除你的原视频。',confirmLabel:'删除记录',danger:true,onConfirm:async()=>{if(uncertain){const receipt=await api('/api/v1/operations/'+key);if(receipt.status!=='succeeded')throw new Error('原删除尚未确认，请稍后核对。');}else{try{await api('/api/v1/records/delete',{method:'POST',commandId:key,body:payload});}catch(e){if(e.unknown)uncertain=true;throw e;}}location.assign(back.href);return true;}});
};
