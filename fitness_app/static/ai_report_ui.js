import {api,keepDialogFocus} from './app.js';
import {el,dateFormat,issueNames,privateError} from './record_common.js';

const $=id=>document.getElementById(id),dialog=$('ai-report-dialog');
const storageKey='fitness-ai-report-pending:'+document.body.dataset.userId;
const active=new Set(['queued','running','cancelling']);
const names={queued:'等待生成',running:'正在生成',cancelling:'正在停止',succeeded:'已生成',failed:'未完成',cancelled:'已停止',uncertain:'费用待核对'};
let opened=false,disposed=false,sequence=0,reportView=0,busy=null,config=null,options=null,current=null,pending=null,page=1,pollTimer=null,opener=null,releaseFocus=null;
const valid=own=>opened&&!disposed&&own===sequence;
const viewing=(own,view)=>valid(own)&&view===reportView;
const title=kind=>kind==='comparison'?'前后对比':'训练复盘';
const rangeText=r=>`本次选定 ${r.selected_records} 条训练，参考 ${r.history_records} 条历史记录。${r.history_enabled?'':'历史参考已关闭。'}`;
function state(text){$('ai-report-state').textContent=text;}
function fail(e){if(disposed)return;$('ai-report-error').textContent=e.message||String(e);$('ai-report-error').hidden=false;privateError(e);}
function controls(){if(!dialog)return;for(const id of ['generate','refresh','history-reference','consent','check','resend','stop','new','delete','delete-yes','history-refresh'])$('ai-report-'+id).disabled=Boolean(busy);$('ai-report-generate').disabled=Boolean(busy)||Boolean(pending)||!options?.provider.available||!(options?.records.length||options?.history?.records_total);$('ai-report-history-reference').disabled=Boolean(busy)||Boolean(pending)||options?.read_scope.history_enabled===false;$('ai-report-refresh').disabled=Boolean(busy)||Boolean(pending);$('ai-report-new').disabled=Boolean(busy)||active.has(current?.status);}
async function action(task){if(busy||disposed)return;const token={};busy=token;controls();$('ai-report-error').hidden=true;const own=sequence;try{await task(own);}catch(e){if(valid(own))fail(e);}finally{if(busy===token){busy=null;controls();}}}
function remember(value){sessionStorage.setItem(storageKey,JSON.stringify(value));pending=value;}
function forget(id){if(pending?.id!==id)return;sessionStorage.removeItem(storageKey);pending=null;}
function restore(){const raw=sessionStorage.getItem(storageKey);if(!raw)return;const value=JSON.parse(raw);if(!/^[0-9a-f-]{36}$/.test(value.id)||!['review','comparison'].includes(value.body?.kind)||value.body.consent!==true||!Array.isArray(value.body.record_ids)||value.body.record_ids.length>20||!value.body.expected_context)throw new Error('上次生成信息无法读取，请查看复盘历史。');pending=value;}
function measurement(row){const unit=row.unit==='ms'?'秒':'次',number=v=>row.unit==='ms'?(v/1000).toFixed(1):v;return `偏差 ${number(row.issue)} / 可评估 ${number(row.evaluated)} ${unit}；无法判断 ${number(row.unknown)} ${unit}`;}
function observations(data,box){box.replaceChildren();if(data.comparison){if(!data.comparison.comparable){box.append(el('p','暂不能作可比对照：'+data.comparison.reason,'notice'));return;}for(const pair of data.comparison.comparisons){const card=el('article',undefined,'ai-report-comparison');card.append(el('strong',(pair.title||issueNames[pair.code]||'参考姿态检查')+(pair.side==='left'?' · 左侧':pair.side==='right'?' · 右侧':'')),el('p','前次：'+measurement(pair.before)),el('p','后次：'+measurement(pair.after)),el('p',pair.interpretation,'field-help'));box.append(card);}}else if(data.history){for(const group of data.history.rule_cohorts){box.append(el('p',(group.title||group.code)+'：'+group.records_with_issue+' / '+group.assessed_records+' 条可评估记录出现提示；'+group.unassessable_records+' 条无法评估。','field-help'));}}else box.append(el('p','本次不引用历史记录。','field-help'));}
async function prepare(own){
 const view=++reportView;options=null;current=null;clearTimeout(pollTimer);
 $('ai-report-result').hidden=true;$('ai-report-options').hidden=true;$('ai-report-consent').checked=false;$('ai-report-options').querySelector('details').open=false;
 state('正在读取训练范围…');
 const value=await api('/api/v1/ai/report-options',{method:'POST',body:config});
 if(!viewing(own,view))return;
 options=value;
 const count=value.records.length||value.history?.records_total||0;
 for(const id of ['range','basis','submit'])$('ai-report-'+id).hidden=!count;
 $('ai-report-history-toggle').hidden=!count&&config.history_scope!=='none';
 $('ai-report-empty').hidden=Boolean(count);
 $('ai-report-title').textContent=title(value.kind);
 $('ai-report-generate').textContent=value.kind==='comparison'?'生成AI对比':'生成AI复盘';
 $('ai-report-count').replaceChildren(el('strong',String(count)),el('span','条训练记录'));
 $('ai-report-scope').textContent=value.kind==='comparison'?'前次、后次训练对比':value.records.length?'已选训练':config.filters?'所选动作与日期':'全部训练记录';
 const dates=[config.filters?.date_from,config.filters?.date_to].filter(Boolean).join(' 至 ');
 $('ai-report-range-label').textContent=dates;$('ai-report-range-label').hidden=!dates;
 const list=$('ai-report-sources');list.replaceChildren();
 for(const [i,record]of value.records.entries()){
  const li=el('li'),link=el('a',(value.kind==='comparison'?(i?'后次 · ':'前次 · '):'')+record.action_name+' · '+dateFormat.format(record.started_at_ms));
  link.href='/records/'+record.session_id;li.append(link);list.append(li);
 }
 if(config.filters)list.append(el('li',dates||'所选动作的全部日期'));
 $('ai-report-history-reference').checked=config.history_scope!=='none'&&value.read_scope.history_enabled;
 $('ai-report-read-scope').textContent=rangeText(value.read_scope);observations(value,$('ai-report-observations'));
 $('ai-report-provider').textContent=value.provider.available?'':'请先在设置中配置 AI 服务。';$('ai-report-provider').hidden=value.provider.available;
 $('ai-report-options').hidden=false;
 state('');controls();
}
function render(row){
 current=row;$('ai-report-options').hidden=true;$('ai-report-result').hidden=false;$('ai-report-delete-confirm').hidden=true;
 const box=$('ai-report-answer');box.replaceChildren();
 $('ai-report-result-title').textContent=title(row.kind)+' · '+(names[row.status]||row.status);
 $('ai-report-stop').hidden=!active.has(row.status);$('ai-report-delete').hidden=row.status!=='succeeded'||row.report_body_deleted;
 if(row.error_code==='AI_SOURCE_DELETED')box.append(el('p','来源训练已删除，这份复盘已失效。','notice'));
 else if(row.report_body_deleted)box.append(el('p','复盘已删除，训练记录仍保留。','notice'));
 else if(row.context_current===false)box.append(el('p','训练记录已变化，请重新生成复盘。','notice'));
 else if(row.result){
  const result=row.result;
  box.append(el('p',result.summary,'ai-report-summary'));
  if(result.next_focus.length){
   const focus=el('section',undefined,'ai-report-focus'),list=el('ol');
   for(const item of result.next_focus)list.append(el('li',item.text));
   focus.append(el('h3','下一次，先关注这些'),list);box.append(focus);
  }
  if(result.facts?.comparison){
   box.append(el('h3','检测观测对照'));observations({comparison:result.facts.comparison},box.appendChild(el('div')));
  }
  const details=el('details',undefined,'ai-report-basis');
  details.append(el('summary','查看详细解读与依据'));
  for(const item of result.explanations){
   const card=el('article',undefined,'ai-evidence-card'),e=item.evidence;
   card.append(el('strong',(e.title||e.message||'已保存的参考观察')+(e.side==='right'?' · 右侧':e.side==='left'?' · 左侧':'')),el('p',item.explanation),el('p','调整：'+item.adjustment));details.append(card);
  }
  details.append(el('p',rangeText(result.read_scope),'field-help'));
  if(result.facts?.selected_records?.length){
   const sources=el('ul',undefined,'ai-report-sources');
   for(const record of result.facts.selected_records){
    const link=el('a',record.action_name+' · '+dateFormat.format(record.started_at_ms));link.href='/records/'+record.session_id;
    const li=el('li');li.append(link);sources.append(li);
   }
   details.append(sources);
  }
  for(const limitation of result.limitations)details.append(el('p',limitation,'field-help'));
  box.append(details);
 }else if(!active.has(row.status))box.append(el('p',row.message||'生成未完成，请重试。','notice'));
 state(active.has(row.status)?'正在生成，可关闭窗口，稍后从历史查看。':row.status==='uncertain'?'生成状态待确认，请稍后查看。':'');controls();
}
async function history(own){
 const value=await api('/api/v1/ai/reports?'+new URLSearchParams({page:String(page),page_size:'10'}));
 if(!valid(own)||value.page!==page)return;
 const box=$('ai-report-history');box.replaceChildren();
 for(const row of value.reports){
  const button=el('button',undefined,'report-history-row');button.type='button';
  button.append(el('strong',title(row.kind)),el('span',dateFormat.format(row.created_at_ms)),el('small',names[row.status]+(row.status==='succeeded'&&!row.body_saved?' · 正文已删除':'')));
  button.onclick=()=>action(token=>show(row.request_id,token,++reportView));box.append(button);
 }
 if(!value.reports.length){
  const empty=el('div',undefined,'ai-report-history-empty');
  empty.append(el('span','还没有复盘'),el('p','生成后会保存在这里'));box.append(empty);
 }
 $('ai-report-prev').disabled=page<=1;$('ai-report-next').disabled=page*value.page_size>=value.total;
 $('ai-report-page').textContent=`${page} / ${Math.max(1,Math.ceil(value.total/value.page_size))}`;
 $('ai-report-pagination').hidden=value.total<=value.page_size;
}
function later(id,own,view=reportView){clearTimeout(pollTimer);if(viewing(own,view))pollTimer=setTimeout(()=>show(id,own,view).catch(e=>{if(viewing(own,view)){fail(e);$('ai-report-pending').hidden=false;state('状态读取失败，请重试。');}}),650);}
async function show(id,own,view=reportView){clearTimeout(pollTimer);const row=await api('/api/v1/ai/reports/'+id);if(!viewing(own,view))return;if(!active.has(row.status))forget(id);$('ai-report-pending').hidden=!pending||pending.id===id;$('ai-report-resend').hidden=true;render(row);if(active.has(row.status))later(id,own,view);else await history(own);}
async function check(own){const view=++reportView;const id=pending?.id||current?.request_id;if(!id)return;try{await show(id,own,view);}catch(e){if(!viewing(own,view))return;if(e.status!==404||!pending)throw e;state('暂未查到生成结果，请稍后查看或重试。');$('ai-report-resend').hidden=false;$('ai-report-options').hidden=false;for(const id of ['range','history-toggle','basis','submit'])$('ai-report-'+id).hidden=false;$('ai-report-empty').hidden=true;$('ai-report-count').textContent='待核对原请求';$('ai-report-range-label').hidden=true;$('ai-report-scope').textContent='原请求：'+pending.body.record_ids.length+'条选定记录，历史范围 '+({none:'不引用',related:'相关训练',all:'筛选内全部历史'}[pending.body.history_scope]||'按原配置')+'。';$('ai-report-sources').replaceChildren();$('ai-report-read-scope').textContent='请先查看上次生成进度。';$('ai-report-observations').replaceChildren();$('ai-report-provider').textContent='';$('ai-report-provider').hidden=true;$('ai-report-consent').checked=false;}}
async function send(own){const view=++reportView,original=pending;try{const row=await api('/api/v1/ai/reports',{method:'POST',commandId:original.id,body:original.body});if(!viewing(own,view))return;render(row);$('ai-report-pending').hidden=true;if(active.has(row.status))later(row.request_id,own,view);else{forget(original.id);await show(row.request_id,own);}await history(own);}catch(e){if(!viewing(own,view))return;if(e.unknown){$('ai-report-pending').hidden=false;state('生成状态待确认，请稍后查看。');}else{forget(original.id);if(e.status===409){options=null;$('ai-report-consent').checked=false;state('范围或设置已变化，请重新核对范围。');}}throw e;}}
export async function openAIReport(input={}){if(!dialog||disposed)return;document.querySelector('#ai-assistant:not([hidden]) #ai-assistant-close')?.click();if($('report-export-dialog')?.open)$('report-export-back').click();opener=document.activeElement;opened=true;const own=++sequence;busy=null;config={kind:input.kind||'review',context_kind:'overview',record_ids:[...(input.record_ids||[])],history_scope:input.kind==='comparison'?'none':input.record_ids?.length?'related':'all',...(input.filters?{filters:input.filters}:{})};options=current=null;page=1;$('ai-report-error').hidden=true;$('ai-report-answer').replaceChildren();$('ai-report-result').hidden=true;$('ai-report-pending').hidden=true;$('ai-report-resend').hidden=true;if(!dialog.open){dialog.showModal();releaseFocus=keepDialogFocus(dialog);document.documentElement.classList.add('modal-open');}$('ai-report-back').focus();try{restore();if(pending){$('ai-report-options').hidden=true;$('ai-report-pending').hidden=false;state('上次生成尚未确认，请先查看进度。');}else await prepare(own);await history(own);}catch(e){if(valid(own))fail(e);}controls();}
function close(){opened=false;sequence++;busy=null;clearTimeout(pollTimer);dialog.close();releaseFocus?.();releaseFocus=null;document.documentElement.classList.remove('modal-open');opener?.focus({preventScroll:true});}
if(dialog){
 $('ai-report-back').onclick=close;dialog.addEventListener('cancel',e=>{e.preventDefault();close();});
 $('ai-report-refresh').onclick=()=>action(own=>{if(pending)throw new Error('请先查看上次生成进度。');return prepare(own);});
 $('ai-report-history-reference').onchange=()=>action(own=>{config.history_scope=$('ai-report-history-reference').checked?(config.record_ids.length?'related':'all'):'none';return prepare(own);});
 $('ai-report-generate').onclick=()=>action(async own=>{if(pending)return check(own);if(!$('ai-report-consent').checked)throw new Error('请先确认发送本次训练摘要。');if(!options)throw new Error('请先核对本次范围。');remember({id:crypto.randomUUID(),body:{...config,expected_versions:options.expected_versions,expected_context:options.expected_context,consent:true}});await send(own);});
 $('ai-report-check').onclick=()=>action(check);$('ai-report-resend').onclick=()=>action(own=>{if(!$('ai-report-consent').checked)throw new Error('请先重新确认发送原请求中的训练摘要。');return send(own);});
 $('ai-report-new').onclick=()=>action(own=>{if(pending)throw new Error('请先查看或停止上次生成。');return prepare(own);});
 $('ai-report-stop').onclick=()=>action(async own=>{const id=current.request_id,latest=await api('/api/v1/ai/requests/'+id);if(!valid(own))return;await api('/api/v1/ai/requests/'+id+'/cancel',{method:'POST',body:{expected_version:latest.state_version}});if(valid(own))await show(id,own);});
 $('ai-report-delete').onclick=()=>{$('ai-report-delete-confirm').hidden=false;};$('ai-report-delete-no').onclick=()=>{$('ai-report-delete-confirm').hidden=true;};
 $('ai-report-delete-yes').onclick=()=>action(async own=>{const id=current.request_id,latest=await api('/api/v1/ai/reports/'+id);if(!valid(own))return;if(!latest.report_body_deleted)await api('/api/v1/ai/reports/'+id,{method:'DELETE',body:{expected_version:latest.state_version}});if(valid(own))await show(id,own);});
 $('ai-report-history-refresh').onclick=()=>action(history);$('ai-report-prev').onclick=()=>action(own=>{page=Math.max(1,page-1);return history(own);});$('ai-report-next').onclick=()=>action(own=>{page++;return history(own);});
 window.addEventListener('fitness-auth-revoked',()=>{sequence++;pending=options=current=null;config=null;try{sessionStorage.removeItem(storageKey);}catch{}$('ai-report-answer').replaceChildren();$('ai-report-history').replaceChildren();$('ai-report-sources').replaceChildren();if(dialog.open)close();});
 window.addEventListener('pagehide',()=>{disposed=true;sequence++;clearTimeout(pollTimer);pending=options=current=null;});
 if(new URLSearchParams(location.search).get('ai_reports')==='1')openAIReport().catch(fail);
}
