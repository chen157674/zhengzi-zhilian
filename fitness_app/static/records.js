import {api,confirmDialog,showMessage} from './app.js';
import {openReportExport} from './report_export_ui.js';
import {openAIReport} from './ai_report_ui.js';
import {openRecordComparison} from './record_comparison.js';
import {el,terminal,dateFormat,quantity,recordStatus,privateError} from './record_common.js';

const form=document.querySelector('#record-filters'),rows=document.querySelector('#record-rows'),table=document.querySelector('#records-table');
const selectPage=document.querySelector('#record-select-page'),deleteSelected=document.querySelector('#record-delete-selected');
const errorBox=document.querySelector('#records-error'),retry=document.querySelector('#records-retry');
const fields=['action_id','source','period','search'],selected=new Set(),pageSize=10;
let records=[],page=1,wantedPage=1,total=0,sequence=0,debounce=null,busy=false;
const initial=new URLSearchParams(location.search);
let preciseRange=Object.fromEntries(['date_from','date_to'].filter(k=>/^\d{4}-\d{2}-\d{2}$/.test(initial.get(k)||'')).map(k=>[k,initial.get(k)]));
const rangeLabel=el('span',undefined,'field-help record-date-range');
rangeLabel.style.gridColumn='1 / -1';rangeLabel.hidden=true;form.append(rangeLabel);
if(Object.keys(preciseRange).length)form.elements.period.append(new Option('指定日期','custom'));
if(initial.has('discarded')){const message=document.querySelector('#records-return-message');message.hidden=false;message.textContent=initial.get('discarded')==='pending'?'已保留现有摘要，请继续保存任务状态。':'已确认放弃未保存部分，仅保留此前已保存摘要。'}
for(const name of fields){if(initial.has(name))form.elements[name].value=initial.get(name)}
if(Object.keys(preciseRange).length){form.elements.period.value='custom';rangeLabel.textContent=[preciseRange.date_from,preciseRange.date_to].filter(Boolean).filter((v,i,a)=>i===0||v!==a[0]).join(' — ');rangeLabel.hidden=false;}
if(!form.elements.period.value)form.elements.period.value='30';
wantedPage=Math.max(1,Number.parseInt(initial.get('page')||'1',10)||1);
function selectable(record){return terminal.has(record.status)&&record.save_state==='clean'}
document.querySelector('#records-export').onclick=()=>openReportExport([...selected]);
document.querySelector('#records-ai-review').onclick=()=>openAIReport({record_ids:[...selected]});
document.querySelector('#records-compare').onclick=()=>openRecordComparison(records.filter(r=>selected.has(r.session_id)).sort((a,b)=>a.started_at_ms-b.started_at_ms).map(r=>r.session_id),{returnTo:contextURL()});
function updateSelection(){
  const pair=records.filter(r=>selected.has(r.session_id)),sameAction=pair.length===2&&pair[0].action_id===pair[1].action_id;
  document.querySelector('#records-compare').disabled=busy||selected.size!==2||!sameAction;
  document.querySelector('#records-compare').title=selected.size===2&&!sameAction?'请选择同一个动作的两条记录；详细规则和机位由服务端继续核对':'比较同项动作记录';
  document.querySelector('#records-export').textContent=selected.size?'导出所选PDF':'训练报告';
  const eligible=records.filter(selectable),count=eligible.filter(item=>selected.has(item.session_id)).length;
  selectPage.checked=eligible.length>0&&count===eligible.length;selectPage.indeterminate=count>0&&count<eligible.length;
  selectPage.disabled=busy||!eligible.length;deleteSelected.disabled=busy||!selected.size;
  document.querySelector('.records-actionbar').dataset.hasSelection=String(selected.size>0);
  document.querySelector('#record-selection').textContent=selected.size?`已选 ${selected.size} 条`:'';
}
function contextURL(){const params=new URLSearchParams({view:'list',page:String(page)});for(const name of fields){const value=form.elements[name].value;if(value)params.set(name,value)}for(const [name,value]of Object.entries(preciseRange))params.set(name,value);return '/records?'+params}
function render(){
  rows.replaceChildren();
  for(const record of records){
    const row=el('tr');row.dataset.sessionId=record.session_id;
    const choice=el('td'),checkbox=el('input');checkbox.type='checkbox';checkbox.setAttribute('aria-label','选择 '+record.action_name+' '+dateFormat.format(record.started_at_ms));checkbox.disabled=busy||!selectable(record);checkbox.checked=selected.has(record.session_id);
    checkbox.addEventListener('change',()=>{if(checkbox.checked)selected.add(record.session_id);else selected.delete(record.session_id);updateSelection()});choice.append(checkbox);
    const identity=el('td');identity.append(el('strong',record.action_name),el('span',dateFormat.format(record.started_at_ms),'row-note'));
    const amount=el('td',quantity(record));if(record.unit==='per_side_reps'||record.action_id==='high_knees')amount.append(el('span',`左 ${record.left_count} · 右 ${record.right_count}`,'row-note'));
    const state=el('td');state.append(el('span',recordStatus(record),'status-badge '+(record.save_state==='save_failed'?'warning':record.status==='completed'?'enabled':'disabled')));if(record.terminal_persisted===false)state.append(el('span','已放弃未保存部分；终态未落库','row-note'));
    const actions=el('td'),group=el('div',undefined,'row-actions'),open=el('a','查看详情','text-link');open.href=`/records/${record.session_id}?returnTo=${encodeURIComponent(contextURL())}`;open.dataset.action='history.open';
    const remove=el('button','删除','danger-link');remove.type='button';remove.disabled=busy||!selectable(record);remove.dataset.action='history.delete';
    if(!selectable(record))remove.title='先结束活动任务并处理保存故障';
    remove.addEventListener('click',()=>deleteRecords([record]).catch(actionError));group.append(open,remove);actions.append(group);
    row.append(choice,identity,el('td',record.source==='camera'?'摄像头':'上传视频'),amount,state,actions);rows.append(row);
  }
  if(!records.length){const row=el('tr'),cell=el('td',undefined,'table-empty');cell.colSpan=6;cell.append(el('p','没有符合条件的记录'));const clear=el('button','清空筛选','button secondary small');clear.type='button';clear.onclick=reset;const start=el('a','开始分析','button');start.href='/realtime';cell.append(clear,start);row.append(cell);rows.append(row)}
  document.querySelector('#record-total').textContent=`共 ${total} 条`;
  document.querySelector('#record-page-label').textContent=`第 ${page} / ${Math.max(1,Math.ceil(total/pageSize))} 页`;
  document.querySelector('#record-prev').disabled=busy||page<=1;document.querySelector('#record-next').disabled=busy||page*pageSize>=total;
  updateSelection();
}
function dateBoundary(days){const formatter=new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'});const parts=Object.fromEntries(formatter.formatToParts(Date.now()).filter(part=>part.type!=='literal').map(part=>[part.type,part.value]));const date=new Date(Date.UTC(+parts.year,+parts.month-1,+parts.day));date.setUTCDate(date.getUTCDate()-days+1);return date.toISOString().slice(0,10)}
async function load(){
  const current=++sequence,query=new URLSearchParams({page:String(wantedPage),page_size:String(pageSize)});
  for(const name of ['action_id','source','search'])if(form.elements[name].value)query.set(name,form.elements[name].value);
  if(Object.keys(preciseRange).length){for(const [name,value]of Object.entries(preciseRange))query.set(name,value);}
  else if(form.elements.period.value!=='all')query.set('date_from',dateBoundary(Number(form.elements.period.value)));
  table.setAttribute('aria-busy','true');errorBox.hidden=retry.hidden=true;
  document.querySelector('#record-prev').disabled=document.querySelector('#record-next').disabled=true;
  try{const data=await api('/api/v1/records?'+query);if(current!==sequence)return;records=data.records;total=data.total;page=wantedPage=data.page;selected.clear();render();history.replaceState(null,'',contextURL())}
  catch(error){if(current!==sequence||privateError(error))return;errorBox.textContent=error.message+'；保留上次已读取结果。';errorBox.hidden=retry.hidden=false;if(!records.length)rows.querySelector('.table-empty').textContent='记录读取失败，请重新加载'}
  finally{if(current===sequence)table.removeAttribute('aria-busy')}
}
function actionError(error){if(!privateError(error))showMessage(error.message)}
async function deleteRecords(items){
  if(busy||!items.length)return;
  busy=true;render();
  const ids=items.map(item=>item.session_id),expected_versions=Object.fromEntries(items.map(item=>[item.session_id,item.state_version]));
  const commandId=crypto.randomUUID();let uncertain=false;
  try{
    const result=await confirmDialog({title:'删除记录',message:`将删除已选 ${items.length} 条摘要（${items.map(item=>item.action_name+' '+dateFormat.format(item.started_at_ms)).join('；')}）。仅删除这些记录，不删除用户原视频。`,confirmLabel:'删除记录',danger:true,onConfirm:async()=>{
      try{if(uncertain){const receipt=await api('/api/v1/operations/'+commandId);if(receipt.status==='succeeded')return receipt.result;throw new Error('请稍后查看原删除结果')}
        return await api('/api/v1/records/delete',{method:'POST',commandId,body:{ids,expected_versions}});
      }catch(error){if(privateError(error))throw error;if(error.unknown){uncertain=true;document.querySelector('[data-dialog-confirm]').textContent='核对原删除';try{const receipt=await api('/api/v1/operations/'+commandId);if(receipt.status==='succeeded')return receipt.result}catch(queryError){privateError(queryError)}throw new Error('请查看原删除操作的结果。')}
        if(error.code==='VERSION_CONFLICT'||error.code==='INVALID_STATE'){await load();throw new Error('记录状态已变化，未执行本批删除。请取消并重新选择。')}throw error;
      }
    }});
    if(result){selected.clear();await load();showMessage(`已删除 ${result.deleted_ids.length} 条摘要。`)}
  }finally{busy=false;render();table.focus()}
}
function changed(){clearTimeout(debounce);sequence++;selected.clear();wantedPage=1;updateSelection()}
function reset(){preciseRange={};rangeLabel.hidden=true;form.querySelector('option[value="custom"]')?.remove();form.reset();changed();load()}
form.addEventListener('submit',event=>{event.preventDefault();changed();load()});
for(const name of ['action_id','source','period'])form.elements[name].addEventListener('change',()=>{if(name==='period'){preciseRange={};rangeLabel.hidden=true;form.querySelector('option[value="custom"]')?.remove();}changed();load()});
form.elements.search.addEventListener('input',()=>{changed();debounce=setTimeout(load,250)});
document.querySelector('#record-clear').onclick=reset;retry.onclick=load;
selectPage.onchange=()=>{for(const record of records.filter(selectable)){if(selectPage.checked)selected.add(record.session_id);else selected.delete(record.session_id)}render()};
deleteSelected.onclick=()=>deleteRecords(records.filter(item=>selected.has(item.session_id))).catch(actionError);
document.querySelector('#record-prev').onclick=()=>{selected.clear();wantedPage=page-1;load()};document.querySelector('#record-next').onclick=()=>{selected.clear();wantedPage=page+1;load()};
load();
