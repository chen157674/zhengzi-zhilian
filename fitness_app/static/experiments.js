import {api,confirmDialog,showMessage} from './app.js';
import {el,dateFormat,privateError} from './record_common.js';
import {predictionTiming} from './experiment_format.js';

const root=document.querySelector('#experiment-page'),taskSelect=document.querySelector('#experiment-task'),datasetSelect=document.querySelector('#experiment-dataset'),start=document.querySelector('#experiment-start');
const errorBox=document.querySelector('#experiment-error'),reload=document.querySelector('#experiment-reload'),cancel=document.querySelector('#experiment-cancel'),again=document.querySelector('#experiment-run-again');
const finalStates=new Set(['completed','cancelled','failed','interrupted']),names={queued:'等待汇总',running:'对比中',cancelling:'正在取消',completed:'已完成对比',cancelled:'已取消',failed:'对比失败',interrupted:'已中断'};
const pendingKey='fitness-experiment-create:'+document.body.dataset.userId,draftKey='fitness-experiment-draft:'+document.body.dataset.userId;
let options=null,selected=new Set(),current=null,eid=root.dataset.experimentId,busy=false,disposed=false,timer=null,pending=null,page=1,historyData=null,pendingReadFailed=false;
try{const raw=sessionStorage.getItem(pendingKey);if(raw){const saved=JSON.parse(raw),p=saved?.payload;if(typeof saved?.key!=='string'||!p||['task_id','dataset_version','split_hash'].some(key=>typeof p[key]!=='string')||!Array.isArray(p.method_ids)||p.method_ids.some(value=>typeof value!=='string'))throw new Error('invalid pending request');pending=saved}}catch{pendingReadFailed=true;showMessage('无法读取待核对请求，尚未发送新请求；请先确认浏览器存储可用。')}
function failed(error){if(privateError(error))return;errorBox.textContent=error.message||'操作尚未确认，请重新读取原任务。';errorBox.hidden=false;reload.hidden=false}
function task(){return options?.tasks.find(item=>item.task_id===taskSelect.value)}
function finished(){return current&&finalStates.has(current.status)&&current.stopped}
function percent(value){return typeof value==='number'&&Number.isFinite(value)?(value*100).toFixed(1)+'%':'不可计算'}
function saveDraft(value={task_id:taskSelect.value,method_ids:[...selected]}){try{sessionStorage.setItem(draftKey,JSON.stringify(value))}catch{/* 选择可继续使用；只有创建标识必须持久化成功。 */}}
function readDraft(){try{const value=JSON.parse(sessionStorage.getItem(draftKey)||'null');return value&&typeof value.task_id==='string'&&Array.isArray(value.method_ids)?value:null}catch{return null}}
function lockControls(){
  const locked=busy||pendingReadFailed||Boolean(current&&!finished())||Boolean(pending);
  taskSelect.disabled=locked||!options?.tasks.length;datasetSelect.disabled=locked||!task();
  for(const box of document.querySelectorAll('#experiment-method-options input'))box.disabled=locked||box.dataset.ready!=='true';
  start.disabled=busy||pendingReadFailed||Boolean(current&&!finished())||(!pending&&(!task()?.ready||selected.size<2));start.textContent=pending?'核对原对比':'开始对比';
  again.disabled=busy||pendingReadFailed||!finished()||current.save_state!=='clean'||!current.terminal_persisted;cancel.disabled=busy||current?.status==='cancelling';
}
function methods(restore=null){
  const info=task(),container=document.querySelector('#experiment-method-options');container.replaceChildren();
  const desired=restore?new Set(restore):new Set(info?.methods.filter(method=>method.ready&&['svm','rf'].includes(method.family)).map(method=>method.method_id)||[]);
  selected=new Set();datasetSelect.replaceChildren();
  if(!info){lockControls();return}
  const option=el('option',info.dataset_name);option.value=info.dataset_version;datasetSelect.append(option);
  document.querySelector('#experiment-data-note').textContent=`任务：${info.negative_label} / ${info.positive_label} 来源片段分类 · 源视频 ${info.available_samples} 段 · 开发 ${info.development_samples} 段 · 固定测试 ${info.samples} 段 · ${info.subject_group_count} 个来源关联组`;
  for(const method of info.methods){
    const label=el('label',undefined,'experiment-method'),box=el('input'),text=el('span');box.type='checkbox';box.value=method.method_id;box.dataset.ready=String(method.ready);box.disabled=!method.ready;
    box.checked=method.ready&&desired.has(method.method_id);if(box.checked)selected.add(method.method_id);
    text.append(el('strong',method.name),el('small',method.ready?'已有冻结预测，可复用':method.reason||'资源未就绪'));label.append(box,text);container.append(label);
    box.onchange=()=>{box.checked?selected.add(box.value):selected.delete(box.value);saveDraft();lockControls()};
  }
  document.querySelector('#experiment-method-error').hidden=true;lockControls();
}
async function loadOptions(restore=null){
  options=await api('/api/v1/admin/experiment-options');taskSelect.replaceChildren();
  for(const item of options.tasks){const option=el('option',item.name);option.value=item.task_id;taskSelect.append(option)}
  if(options.status!=='ready'){document.querySelector('#experiment-data-note').textContent=options.message;methods();return}
  if(restore?.task_id&&options.tasks.some(item=>item.task_id===restore.task_id))taskSelect.value=restore.task_id;
  methods(restore?.method_ids||null);
}
function cell(row,text,help){const node=el('td',text);if(help)node.append(el('small',help));row.append(node);return node}
function table(headers,rows,caption){
  const result=el('table');result.append(el('caption',caption));const head=el('thead'),hr=el('tr');for(const label of headers)hr.append(el('th',label));head.append(hr);result.append(head);
  const body=el('tbody');for(const values of rows){const row=el('tr');for(const value of values)row.append(el('td',String(value)));body.append(row)}result.append(body);return result;
}
function renderResult(){
  if(!current)return;
  const config=current.config,result=current.results,done=finished();
  document.querySelector('#experiment-config').hidden=true;document.querySelector('#experiment-empty').hidden=true;document.querySelector('#experiment-result').hidden=false;
  document.querySelector('#experiment-title').textContent='实验结果';document.querySelector('#experiment-subtitle').textContent=config.task_name;
  const badge=document.querySelector('#experiment-badge');badge.hidden=false;badge.textContent=names[current.status]||current.status;badge.className='status-badge '+(current.status==='completed'?'enabled':current.status==='failed'?'warning':'disabled');
  const note=document.querySelector('#experiment-run-note');note.classList.toggle('warning',current.status==='failed'||current.save_state!=='clean');
  note.textContent=current.save_state!=='clean'?'任务已停止，失败状态尚未确认保存；恢复存储后重新读取。':current.status==='completed'?'已复用冻结预测并完成汇总。本轮未选出达标主方法；具体结果见下表。':current.status==='failed'?'部分方法未完成，已保存的方法结果仍保留。':current.status==='cancelled'?'已取消对比，仅保留停止前完成的方法。':current.status==='interrupted'?'原任务已中断，仅保留确认保存的方法。':'正在复用冻结原始预测并汇总，后台切换或刷新不会重新运行。';
  const meta=document.querySelector('#experiment-meta');meta.replaceChildren();for(const [label,value] of [['源视频',config.available_samples+' 段'],['开发',config.development_samples+' 段'],['测试',config.samples+' 段'],['来源关联组',config.subject_group_count+' 组'],['已处理',`${result.processed_methods??0} / ${result.total_methods??config.method_ids.length} 方法`]]){const item=el('span',label);item.append(el('strong',value));meta.append(item)}
  const progress=document.querySelector('#experiment-progress');progress.max=result.total_methods||config.method_ids.length;progress.value=result.processed_methods||0;document.querySelector('#experiment-progress-wrap').hidden=done;
  document.querySelector('#experiment-progress-text').textContent=`${result.processed_methods??0} / ${result.total_methods??config.method_ids.length} 个方法已处理`;
  const rows=document.querySelector('#experiment-result-rows'),details=document.querySelector('#experiment-method-details');rows.replaceChildren();details.replaceChildren();
  for(const method of result.methods||[]){
    const row=el('tr'),definition=config.methods?.find(item=>item.method_id===method.method_id),label=definition?.name||method.method_id;cell(row,label);
    const status=el('td'),tag=el('span',method.status==='completed'?'已完成':method.status==='failed'?'汇总失败':'未运行','status-badge '+(method.status==='failed'?'warning':'disabled'));status.append(tag);
    if(method.status==='completed')status.append(el('small',method.metrics.acceptance_passed?'达到该来源任务门槛':'未达目标'));
    if(method.message)status.append(el('small',method.message));row.append(status);
    const metric=method.status==='completed'?method.metrics.main:null;
    for(const key of ['positive_precision','positive_recall','macro_f1','coverage'])cell(row,metric?percent(metric[key]):'—',metric&&metric[key]===null?'无适用分母':undefined);
    const timing=predictionTiming(method.predictions,config.prediction_timing);
    cell(row,method.status==='completed'?timing.text:'—',method.status==='completed'?timing.note:undefined);rows.append(row);
    if(!metric)continue;
    const section=el('section',undefined,'experiment-method-detail');section.append(el('h3',label));const grid=el('div',undefined,'experiment-detail-grid');
    grid.append(table(['真实标签',`预测 ${config.negative_label}`,`预测 ${config.positive_label}`,'拒判'],metric.confusion.counts.map((values,index)=>[index?config.positive_label:config.negative_label,...values]),'原始混淆计数'));
    grid.append(table(['来源组','样本','可判断','主宏 F1'],Object.entries(method.metrics.groups.subject_group).map(([group,value])=>[group,value.samples,value.covered,percent(value.macro_f1)]),'按来源组查看'));
    section.append(grid,el('p',`主指标分母 ${metric.samples} 段，拒判 ${metric.rejected} 段。${method.metrics.conditional?'可判断子集的条件宏 F1：'+percent(method.metrics.conditional.macro_f1)+'。':'没有可判断子集。'}`,'field-help'));details.append(section);
  }
  document.querySelector('#experiment-scope').textContent=`正类为 ${config.positive_label}；指标只针对来源整段标签，不代表全部姿态问题。分类预测耗时不含共同姿态提取；本次仅复用缓存。原评估：${dateFormat.format(config.source_evaluated_at_ms)}。`;
  const missing=document.querySelector('#experiment-unavailable');missing.replaceChildren();const labels={keypoint_precision:'关键点精度',cycle_count_accuracy:'连续计数准确率',specific_issue_accuracy:'具体错误准确率',localization_accuracy:'逐帧问题定位'};
  for(const [key,reason] of Object.entries(config.unavailable_metrics||{}))missing.append(el('li',`${labels[key]||key}：${reason}`));
  cancel.hidden=done;again.textContent='重新运行';saveDraft({task_id:config.task_id,method_ids:config.method_ids});lockControls();
}
async function loadHistory(){
  historyData=await api(`/api/v1/admin/experiments?page=${page}&page_size=5`);const list=document.querySelector('#experiment-history-list');list.replaceChildren();
  for(const item of historyData.experiments){const row=el('div',undefined,'experiment-history-item'),text=el('div');text.append(el('strong',item.config.task_name),el('p',`${dateFormat.format(item.created_at_ms)} · ${names[item.status]||item.status}`));const link=el('a','查看结果 →','text-link');link.href='/admin/experiments/'+item.experiment_id;link.setAttribute('data-nav','');row.append(text,link);list.append(row)}
  document.querySelector('.experiment-history').hidden=historyData.total===0;if(!historyData.experiments.length)list.append(el('p','暂无本人实验记录。','muted'));
  document.querySelector('#experiment-history-total').textContent=`共 ${historyData.total} 次`;const pages=Math.max(1,Math.ceil(historyData.total/5));document.querySelector('#experiment-history-page').textContent=`${page} / ${pages}`;
  document.querySelector('#experiment-history-prev').disabled=page<=1;document.querySelector('#experiment-history-next').disabled=page>=pages;document.querySelector('.experiment-history .pagination').hidden=pages<=1;
}
async function refresh(){
  clearTimeout(timer);if(!eid||disposed)return;
  try{current=await api('/api/v1/admin/experiments/'+eid);renderResult();errorBox.hidden=reload.hidden=true}
  catch(error){failed(error)}
  if(!disposed&&(!current||!finished()||current.save_state!=='clean'||!current.terminal_persisted))timer=setTimeout(refresh,700);
}
async function run(action){if(busy)return;busy=true;errorBox.hidden=true;lockControls();try{await action()}catch(error){failed(error)}finally{busy=false;lockControls()}}
async function create(payload){
  if(!pending){pending={key:crypto.randomUUID(),payload};try{sessionStorage.setItem(pendingKey,JSON.stringify(pending))}catch{pending=null;throw new Error('无法保存本页创建标识，尚未发起实验。')}}
  try{current=await api('/api/v1/admin/experiments',{method:'POST',commandId:pending.key,body:pending.payload})}
  catch(error){if(!error.unknown){pending=null;sessionStorage.removeItem(pendingKey)}throw error}
  eid=current.experiment_id;pending=null;sessionStorage.removeItem(pendingKey);window.history.replaceState(null,'','/admin/experiments/'+eid);renderResult();document.querySelector('#experiment-title').focus({preventScroll:true});window.scrollTo({top:0,behavior:'instant'});await refresh();await loadHistory();
}
async function cancelCurrent(){
  await refresh();if(finished())return;
  current=await api(`/api/v1/jobs/${current.job_id}/cancel`,{method:'POST',commandId:crypto.randomUUID(),body:{expected_version:current.state_version}});renderResult();
  for(let attempt=0;attempt<20&&!current.stopped;attempt++){await new Promise(resolve=>setTimeout(resolve,100));await refresh()}
  if(!current.stopped||current.save_state!=='clean'||!current.terminal_persisted)throw new Error('仍在停止或保存尚未确认，请继续核对原任务。');
}
start.onclick=()=>run(async()=>{
  const info=task();if(!pending&&(!info?.ready||selected.size<2)){document.querySelector('#experiment-method-error').textContent='请选择至少两种不同的就绪方法。';document.querySelector('#experiment-method-error').hidden=false;return}
  await create(pending?.payload||{task_id:info.task_id,dataset_version:info.dataset_version,split_hash:info.split_hash,method_ids:[...selected]});
});
taskSelect.onchange=()=>{methods();saveDraft()};
again.onclick=()=>run(async()=>{const config=current.config;await loadOptions({task_id:config.task_id,method_ids:config.method_ids});const info=task();if(selected.size!==config.method_ids.length||!info.ready)throw new Error('原选择有资源未就绪，请返回配置核对。');await create({task_id:info.task_id,dataset_version:info.dataset_version,split_hash:info.split_hash,method_ids:[...selected]})});
cancel.onclick=()=>run(()=>confirmDialog({title:'取消对比',message:'保留已确认保存的方法结果，未完成方法不会补入成绩。',confirmLabel:'取消并保留已完成结果',onConfirm:async()=>{await cancelCurrent();await loadHistory();return true}}));
reload.onclick=()=>run(async()=>{if(eid)await refresh();else await loadOptions(readDraft()||{task_id:taskSelect.value,method_ids:[...selected]});await loadHistory()});
document.querySelector('#experiment-back').onclick=()=>{if(current){taskSelect.value=current.config.task_id;selected=new Set(current.config.method_ids);saveDraft()}};
for(const [id,delta] of [['experiment-history-prev',-1],['experiment-history-next',1]])document.querySelector('#'+id).onclick=()=>run(async()=>{page+=delta;await loadHistory()});
window.fitnessLeaveGuard=async()=>{
  if(pending){showMessage('请先核对原实验创建结果，再离开。');return false}
  if(current&&!finished())return confirmDialog({title:'离开正在进行的对比',message:'取消并保留已完成结果后再离开；取消此窗口则继续原任务。',confirmLabel:'取消并保留结果后离开',onConfirm:async()=>{await cancelCurrent();return true}});
  if(current&&(current.save_state!=='clean'||!current.terminal_persisted)){showMessage('任务保存尚未确认，请先重新读取。');return false}return true;
};
window.addEventListener('pagehide',()=>{disposed=true;clearTimeout(timer)});
(async()=>{try{await loadOptions(pending?.payload||readDraft());await loadHistory();if(eid)await refresh();else lockControls()}catch(error){failed(error)}})();
