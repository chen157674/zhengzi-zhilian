import {api,keepDialogFocus} from './app.js';
import {el,privateError} from './record_common.js';
const $=id=>document.getElementById(id),dialog=$('review-dialog');
let facts=null,generation=0,task=null,pending=null,timer=null,displayed=null,disposed=false,stopped=false,release=null,opener=null,creating=false,stopping=false;
const day=value=>new Date(value+'T12:00:00');
function iso(date){return date.getFullYear()+'-'+String(date.getMonth()+1).padStart(2,'0')+'-'+String(date.getDate()).padStart(2,'0');}
function monday(value){const date=day(value);date.setDate(date.getDate()-((date.getDay()+6)%7));return iso(date);}
const today=()=>new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
function message(error,job=false){if(privateError(error))return;const node=$(job?'review-job-error':'review-error');node.textContent=error.message;node.hidden=false;if(!job)$('review-retry').hidden=false;}
function controls(){const running=Boolean(task)||creating;$('review-generate').disabled=running||!facts||!facts.actual_training.analysis_record_count&&!facts.actual_training.execution_session_count;$('review-generate').textContent=pending?'核对原任务':displayed?'重新解读':'生成本周回顾';$('review-stop').hidden=!running&&!pending;$('review-open').disabled=!facts;$('review-copy').hidden=!displayed;}
function clearResult(){displayed=null;$('review-answer').replaceChildren();$('review-evidence-links').replaceChildren();$('review-evidence').hidden=true;$('review-copy').hidden=true;$('review-job-error').hidden=true;}
function paint(row){
 const labels={queued:'等待整理',running:'正在回顾本周训练…',cancelling:'已请求停止，正在核对',cancelled:'已停止',failed:'请重试生成训练回顾',source_invalid:'训练数据已变化，请重新读取',expired:'本次回顾已过期',ready:stopped?'原任务已经结束，本次未采用结果':'本周回顾已生成'};
 $('review-run').textContent=labels[row.status]||'正在核对任务';$('review-open-status').textContent=dialog.open?'':labels[row.status]||'';
 if(row.status==='ready'&&row.result&&!stopped){
  displayed=task||row.id;const host=$('review-answer');host.replaceChildren(el('h3',row.result.summary));
  if(row.result.focus?.length){host.append(el('h4','下次关注'));for(const line of row.result.focus)host.append(el('p',line));}
  const refs=$('review-evidence-links');refs.replaceChildren();for(const id of row.result.evidence_refs||[]){const a=el('a','查看依据记录 →','text-link');a.href='/records/'+id;a.dataset.nav='';refs.append(a);}
  for(const id of row.result.knowledge_refs||[]){const a=el('a','查看知识来源 →','text-link');a.href='/coach/knowledge/'+encodeURIComponent(id);a.dataset.nav='';refs.append(a);}
  $('review-evidence').hidden=!refs.children.length;
 }
 if(['source_invalid','failed','expired','cancelled'].includes(row.status))clearResult();
}
async function watch(id,own){
 clearTimeout(timer);if(disposed||own!==generation||task!==id)return;
 try{const row=await api('/api/v1/training/review-jobs/'+id);if(disposed||own!==generation||task!==id)return;paint(row);
  if(['queued','running','cancelling'].includes(row.status))timer=setTimeout(()=>watch(id,own),document.hidden?1600:800);
  else{task=pending=null;controls();}
 }catch(error){if(own!==generation||disposed)return;message(error,true);if(pending){task=null;$('review-run').textContent='请查看原任务结果。';}else if([404,410].includes(error.status)){task=null;clearResult();}controls();}
}
async function stop(){
 if(stopping)return;const id=task||pending?.id;if(!id)return;stopping=true;stopped=true;
 try{const row=await api('/api/v1/training/review-jobs/'+id+'/cancel',{method:'POST',body:{}});if(disposed)return;paint(row);if(['queued','running','cancelling'].includes(row.status)){task=id;watch(id,generation);}else{task=pending=null;clearTimeout(timer);}}
 catch(error){if(!disposed){message(error,true);$('review-run').textContent='请查看原任务的停止状态。';$('review-open-status').textContent='停止结果待核对';}}
 finally{stopping=false;controls();}
}
async function generate(){
 if(!facts||creating||task&&!pending)return;creating=true;const own=generation;clearResult();if(!pending){stopped=false;pending={id:crypto.randomUUID(),body:{week_start:facts.week_start,data_version:facts.data_version,consent:true,history_consent:true}};}
 const original=pending;controls();
 try{const row=await api('/api/v1/training/review-jobs',{method:'POST',commandId:original.id,body:original.body});if(disposed||own!==generation){api('/api/v1/training/review-jobs/'+row.id+'/cancel',{method:'POST',body:{}}).catch(()=>{});return;}task=row.id;pending=null;if(stopped)await stop();else watch(task,own);}
 catch(error){if(own!==generation||disposed)return;if(error.unknown){task=original.id;watch(task,own);}else{pending=null;message(error,true);}}
 finally{creating=false;controls();}
}
async function loadFacts(){
 const own=++generation;clearTimeout(timer);clearResult();facts=null;$('review-error').hidden=true;$('review-retry').hidden=true;$('review-open-status').textContent='';controls();window.dispatchEvent(new CustomEvent('fitness-review-loading'));
 try{const data=await api('/api/v1/training/reviews/week?'+new URLSearchParams({week_start:$('review-week').value}));if(disposed||own!==generation)return;facts=data;window.fitnessReviewFacts=data;$('review-dialog-scope').textContent=data.week_start+' — '+data.week_end;window.dispatchEvent(new CustomEvent('fitness-review-loaded',{detail:data}));
  const actual=data.actual_training;$('review-generate').disabled=!actual.analysis_record_count&&!actual.execution_session_count;
 }catch(error){if(own===generation&&!disposed){message(error);window.dispatchEvent(new CustomEvent('fitness-review-failed'));}}finally{controls();if(facts&&!facts.actual_training.analysis_record_count&&!facts.actual_training.execution_session_count)$('review-generate').disabled=true;}
}
async function changeWeek(value){
 const previous=facts?.week_start||$('review-week').value;if(task||pending||creating){await stop();if(task||pending||creating){$('review-week').value=previous;$('review-open-status').textContent='请先核对正在停止的回顾任务。';return;}}
 $('review-week').value=monday(value);loadFacts();
}
$('review-week').onchange=() => changeWeek($('review-week').value);
for(const [id,offset]of [['review-prev-week',-7],['review-next-week',7],['review-this-week',0]])$(id).onclick=()=>{const date=day(offset?$('review-week').value:today());if(offset)date.setDate(date.getDate()+offset);changeWeek(iso(date));};
$('review-retry').onclick=loadFacts;$('review-generate').onclick=generate;$('review-stop').onclick=stop;
$('review-open').onclick=()=>{if(dialog.open)return;opener=document.activeElement;dialog.showModal();release=keepDialogFocus(dialog);$('review-open-status').textContent='';if(task)watch(task,generation);controls();if(facts&&!facts.actual_training.analysis_record_count&&!facts.actual_training.execution_session_count){$('review-generate').disabled=true;$('review-run').textContent='本周还没有可回顾的训练记录。';}};
async function close(){if(task||pending||creating)await stop();release?.();release=null;dialog.close();opener?.focus({preventScroll:true});}
$('review-dialog-close').onclick=$('review-dialog-back').onclick=close;dialog.addEventListener('cancel',event=>{event.preventDefault();close();});
$('review-copy').onclick=async()=>{try{await navigator.clipboard.writeText($('review-answer').innerText);$('review-copy').textContent='已复制';}catch{message(new Error('复制未完成，可以选中文字复制。'),true);}};
window.addEventListener('fitness-growth-scope-change',()=>{if(task||pending)stop();clearResult();});
window.addEventListener('focus',async()=>{if(!displayed||task||pending||disposed)return;const id=displayed;try{const row=await api('/api/v1/training/review-jobs/'+id);if(displayed===id&&row.status!=='ready'){clearResult();$('review-run').textContent='原回顾依据已变化，请重新读取。';}}catch(error){message(error,true);}});
const initial=new URLSearchParams(location.search).get('week_start');$('review-week').value=monday(/^\d{4}-\d{2}-\d{2}$/.test(initial||'')?initial:today());loadFacts();
window.addEventListener('pagehide',()=>{const id=task||pending?.id;if(id)api('/api/v1/training/review-jobs/'+id+'/cancel',{method:'POST',body:{}}).catch(()=>{});disposed=true;generation++;clearTimeout(timer);});
window.addEventListener('pageshow',event=>{if(event.persisted){disposed=false;task=pending=null;loadFacts();}});window.addEventListener('fitness-auth-revoked',()=>{disposed=true;generation++;clearTimeout(timer);facts=task=pending=null;clearResult();dialog.close();release?.();});
