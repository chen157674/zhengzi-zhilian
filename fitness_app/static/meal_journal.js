import {api,confirmDialog,keepDialogFocus} from './app.js';
import {el,privateError} from './record_common.js';
import {loadFoodAssets,resolveFoodAsset,foodPicture} from './food_assets.js';
import {mountMealEditor,mealToday} from './meal_entry.js';
const $=id=>document.getElementById(id),page=$('meal-journal-page'),kinds={food_like:'喜欢吃',food_dislike:'不吃的食物',body:'身体信息',goal:'训练目标'};
let current=0,disposed=false,detailRelease=null,detailOpener=null,customStart=null;
const editor=mountMealEditor({onSaved:async row=>{$('journal-date').value=row.consumed_date;customStart=null;await load();}});
function shift(value,offset){const d=new Date(value+'T12:00:00');d.setDate(d.getDate()+offset);return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0');}
function fail(e){if(privateError(e))return;$('journal-error').textContent=e.message;$('journal-error').hidden=false;$('journal-retry').hidden=false;}
async function detail(id,trigger){
 try{
  const row=await api('/api/v1/nutrition/journal/'+id),dialog=$('journal-detail-dialog'),host=$('journal-detail-content');host.replaceChildren();$('journal-detail-title').textContent=row.consumed_date+' · '+row.meal_label;
  const list=el('ul',undefined,'journal-detail-foods');for(const item of row.foods){const line=el('li');line.append(el('strong',item.name),el('span',item.amount||'未填写分量','field-help'));list.append(line);}host.append(list);
  if(row.note)host.append(el('h3','我的备注'),el('p',row.note));
  if(row.summary||row.analysis?.result){
   const section=el('details');section.append(el('summary',row.analysis_stale?'调整前的分析':'已保存的饮食分析'));
   if(row.summary)section.append(el('p',row.summary));if(row.analysis?.result?.next_meal)section.append(el('h3','原分析建议的下一餐'),el('p',row.analysis.result.next_meal));
   for(const item of row.analysis?.result?.swaps||[])section.append(el('p',item.from+' → '+item.to+'：'+item.reason));
   for(const ref of row.analysis?.references||[])section.append(el('p',ref.title||ref.source?.citation||ref.citation_id||'原分析来源','field-help'));host.append(section);
  }
  detailOpener=trigger;dialog.showModal();detailRelease=keepDialogFocus(dialog);
 }catch(e){fail(e);}
}
function closeDetail(){detailRelease?.();detailRelease=null;$('journal-detail-dialog').close();detailOpener?.focus({preventScroll:true});}
$('journal-detail-close').onclick=closeDetail;$('journal-detail-dialog').addEventListener('cancel',e=>{e.preventDefault();closeDetail();});
function renderMemory(memory){
 const card=el('article',undefined,'journal-memory-card'),copy=el('strong',memory.value),actions=el('div',undefined,'journal-memory-actions'),edit=el('button','修改','text-link'),remove=el('button','删除','danger-link');edit.type=remove.type='button';card.append(el('small',kinds[memory.kind]||'个人信息','field-help'),copy);actions.append(edit,remove);card.append(actions);
 edit.onclick=()=>{
  if(card.querySelector('input'))return;const input=el('input'),save=el('button','保存','button secondary small'),cancel=el('button','取消','text-link'),form=el('form',undefined,'journal-memory-editor'),status=el('p',undefined,'approved-dialog-error');input.value=memory.value;input.maxLength=200;input.required=true;input.setAttribute('aria-label','修改'+(kinds[memory.kind]||'记忆'));save.type='submit';cancel.type='button';form.append(input,save,cancel,status);copy.hidden=actions.hidden=true;card.append(form);input.focus();
  cancel.onclick=()=>{form.remove();copy.hidden=actions.hidden=false;edit.focus();};
  form.onsubmit=async event=>{event.preventDefault();save.disabled=true;try{await api('/api/v1/account/memories/'+memory.id,{method:'PATCH',body:{expected_version:memory.version,value:input.value.trim()}});await load();}catch(e){save.disabled=false;if(!privateError(e))status.textContent=e.message;}};
 };
 remove.onclick=()=>confirmDialog({title:'删除这条记忆？',message:memory.value,confirmLabel:'删除记忆',danger:true,onConfirm:async()=>{await api('/api/v1/account/memories/'+memory.id,{method:'DELETE',body:{expected_version:memory.version}});await load();return true;}});
 return card;
}
async function load(){
 const own=++current,end=$('journal-date').value,days=Number($('journal-period').value);if(!end)return;
 const start=customStart||shift(end,-days+1);$('journal-error').hidden=true;$('journal-retry').hidden=true;$('journal-records').setAttribute('aria-busy','true');
 try{
  const [journal,memories]=await Promise.all([api('/api/v1/nutrition/journal?'+new URLSearchParams({date_from:start,date_to:end})),api('/api/v1/account/memories'),loadFoodAssets()]);
  if(own!==current||disposed)return;$('journal-date-label').textContent=start===end?end.replaceAll('-','.'):(start.slice(5)+' — '+end.slice(5));$('journal-next').disabled=end>=mealToday();const host=$('journal-records');host.replaceChildren();let previous=null;
  for(const record of journal.records){
   if(record.consumed_date!==previous){host.append(el('h2',(record.consumed_date===mealToday()?'今天 · ':'')+record.consumed_date.replaceAll('-','.'),'journal-day-heading'));previous=record.consumed_date;}
   const card=el('article',undefined,'journal-meal');card.dataset.recordId=record.id;const first=record.foods[0],picture=first&&foodPicture(resolveFoodAsset(first.name,first.group));
   if(picture){picture.className='journal-meal-thumb';picture.alt=first.name+'示意';card.append(picture);}else card.append(el('span','餐食','field-help'));
   const copy=el('div',undefined,'journal-meal-content');copy.append(el('h3',record.meal_label),el('p',record.foods.map(x=>x.name).join('、')),el('p',record.foods.map(x=>x.name+(x.amount?' '+x.amount:'')).join(' · '),'field-help'));
   if(record.note)copy.append(el('p',record.note));if(record.analysis_stale)copy.append(el('small','食品已修改，原分析仅保留作历史','field-help'));card.append(copy);
   const actions=el('div',undefined,'journal-meal-actions'),view=el('button','查看'),edit=el('button','修改'),remove=el('button','删除');view.type=edit.type=remove.type='button';remove.className='danger-link';view.onclick=()=>detail(record.id,view);edit.onclick=async()=>{try{const current=await api('/api/v1/nutrition/journal/'+record.id);editor.open(current,edit);}catch(e){fail(e);}};
   const commandId=crypto.randomUUID();remove.onclick=()=>confirmDialog({title:'删除这条餐食？',message:record.consumed_date+' '+record.meal_label+'：'+record.foods.map(x=>x.name).join('、'),confirmLabel:'确认删除',danger:true,onConfirm:async()=>{await api('/api/v1/nutrition/journal/'+record.id,{method:'DELETE',commandId,body:{expected_version:record.version}});await load();return true;}});
   actions.append(view,edit,remove);card.append(actions);host.append(card);
  }
  if(!journal.records.length){const empty=el('div',undefined,'approved-notice'),add=el('button','记下这一餐','button');add.type='button';add.onclick=()=>editor.open({consumed_date:end>mealToday()?mealToday():end},add);empty.append(el('h2','这段时间还没有餐食记录'),el('p','从实际吃过的一餐开始。','field-help'),add);host.append(empty);}
  $('journal-memories').replaceChildren(...memories.memories.map(renderMemory));if(!memories.memories.length)$('journal-memories').append(el('p','还没有保存口味和忌口。可以在AI咨询时明确告诉教练，记录后可在这里修改。','field-help'));
  page.dataset.ready='true';
 }catch(e){if(own===current&&!disposed)fail(e);}finally{if(own===current)$('journal-records').setAttribute('aria-busy','false');}
}
$('journal-add-meal').onclick=()=>editor.open({},$('journal-add-meal'));
const query=new URLSearchParams(location.search);let end=query.get('date')||query.get('date_to')||mealToday();const validDate=value=>/^\d{4}-\d{2}-\d{2}$/.test(value||'')&&!Number.isNaN(Date.parse(value+'T12:00:00'));if(!validDate(end))end=mealToday();$('journal-date').value=end;if(validDate(query.get('date_from'))&&query.get('date_from')<=end)customStart=query.get('date_from');
if(query.get('date'))$('journal-period').value='1';
else if(query.get('date_from')){const days=Math.round((new Date(end+'T12:00:00')-new Date(query.get('date_from')+'T12:00:00'))/86400000)+1;if([1,7,30].includes(days))$('journal-period').value=String(days);}
$('journal-date').onchange=$('journal-period').onchange=()=>{customStart=null;load();};$('journal-retry').onclick=load;
$('journal-prev').onclick=()=>{customStart=null;$('journal-date').value=shift($('journal-date').value,-Number($('journal-period').value));load();};
$('journal-next').onclick=()=>{customStart=null;$('journal-date').value=[shift($('journal-date').value,Number($('journal-period').value)),mealToday()].sort()[0];load();};
load();window.addEventListener('pagehide',()=>{disposed=true;current++;});window.addEventListener('pageshow',event=>{if(event.persisted){disposed=false;load();}});window.addEventListener('fitness-auth-revoked',()=>{disposed=true;current++;closeDetail();page.replaceChildren();});
