import {api,beforeLeave,keepDialogFocus} from './app.js';
import {el,privateError} from './record_common.js';
const $=id=>document.getElementById(id),page=$('review-page'),charts=new Map();
let facts=null,data=null,days='7',sequence=0,disposed=false,chosen=0,activePanel='training';
const axis={axisLine:{show:false},axisTick:{show:false},axisLabel:{color:'#87786d',fontSize:11},splitLine:{lineStyle:{color:'#e8dfd6'}}};
function link(label,href,cls='text-link'){const node=el('a',label,cls);node.href=href;node.dataset.nav='';return node;}
async function navigate(href){try{if(await beforeLeave(href))location.assign(href);}catch(error){fail(error);}}
function fail(error){if(privateError(error))return;page.dataset.growthState='failed';page.setAttribute('aria-busy','false');$('growth-error').textContent=error.message;$('growth-error').hidden=false;$('growth-retry').hidden=false;}
function chart(id,option,onClick){
 const host=$(id);if(!host.getClientRects().length)return;
 if(!window.echarts){host.replaceChildren(el('p','正在加载图表，可先查看文字数据。','field-help'));return;}
 let instance=charts.get(id);if(!instance){instance=echarts.init(host,null,{renderer:'svg'});charts.set(id,instance);}
 instance.setOption({animation:!matchMedia('(prefers-reduced-motion:reduce)').matches,animationDuration:160,textStyle:{fontFamily:'Microsoft YaHei,sans-serif',color:'#68584a'},color:['#bf572a','#b9aea0'],...option},true);
 instance.off('click');if(onClick)instance.on('click',onClick);
}
function renderMetrics(){
 const host=$('review-summary');host.replaceChildren();
 const items=activePanel==='diet'?[['本期饮食记录',data.diet_days,'天有记录'],['',data.diet_records,'条餐食']]:[['训练',data.training_days,'天'],['计划执行',data.execution_count,'场'],['动作分析',data.analysis_count,'条']];
 host.hidden=activePanel==='actions'||(activePanel==='diet'?!data.diet_records:!data.analysis_count&&!data.execution_count);
 for(const [label,value,unit]of items){const item=el('div',undefined,'growth-small-metric');item.append(el('span',label),el('strong',String(value)),el('span',unit));host.append(item);}
}
async function openWorkoutDay(date){
 try{
  const result=await api('/api/v1/training/workouts?'+new URLSearchParams({date_from:date,date_to:date,limit:'50'})),items=result.items.filter(x=>x.actual_started_at_ms!==null);
  if(items.length===1)return navigate('/training/workouts/'+items[0].id);
  const dialog=el('dialog',undefined,'growth-day-dialog'),heading=el('div',undefined,'approved-dialog-heading'),close=el('button','×','approved-close');close.type='button';close.setAttribute('aria-label','关闭当日执行');heading.append(el('h2',date+' · 计划执行'),close);dialog.append(heading);
  for(const item of items)dialog.append(link(item.target_snapshot.session_title+' →','/training/workouts/'+item.id));if(!items.length)dialog.append(el('p','该日期的执行记录可能已变化，请重新读取。','field-help'));
  document.body.append(dialog);dialog.showModal();const release=keepDialogFocus(dialog);const dismiss=()=>{release();dialog.close();dialog.remove();};close.onclick=dismiss;dialog.addEventListener('cancel',event=>{event.preventDefault();dismiss();});
 }catch(error){fail(error);}
}
function renderTraining(){
 const has=Boolean(data.analysis_count||data.execution_count);$('growth-training-empty').hidden=has;$('growth-training-chart').hidden=!has;$('growth-headline').hidden=!has;
 $('growth-headline').textContent='本期有 '+data.training_days+' 天留下训练记录。';$('growth-training-data').textContent=data.daily.map(x=>x.date+'，计划执行'+x.workout_count+'场、动作分析'+x.analysis_count+'条').join('；');
 $('growth-records-link').href='/records?'+new URLSearchParams({view:'list',date_from:data.date_from,date_to:data.date_to});
 if(has)chart('growth-training-chart',{tooltip:{trigger:'axis',renderMode:'richText'},legend:{bottom:0,itemWidth:10,itemHeight:8,textStyle:{fontSize:11}},grid:{top:18,right:18,bottom:58,left:38},xAxis:{...axis,type:'category',data:data.daily.map(x=>x.date.slice(5))},yAxis:{...axis,type:'value',minInterval:1},series:[{name:'计划执行（场）',type:'bar',barMaxWidth:23,data:data.daily.map(x=>x.workout_count),itemStyle:{borderRadius:0}},{name:'动作分析（条）',type:'bar',barMaxWidth:23,data:data.daily.map(x=>x.analysis_count),itemStyle:{borderRadius:0}}]},value=>{const day=data.daily[value.dataIndex];if(!day)return;if(value.seriesName==='计划执行（场）')openWorkoutDay(day.date);else navigate('/records?'+new URLSearchParams({view:'list',date_from:day.date,date_to:day.date}));});
}
function renderActions(){
 const selected=$('review-action-filter').value,groups=(data.trend_groups||[]).filter(x=>!selected||x.action_id===selected).sort((a,b)=>Number(Boolean(b.comparable_view&&b.totals.evaluated))-Number(Boolean(a.comparable_view&&a.totals.evaluated))),has=Boolean(data.actions.length||groups.length||data.retests?.completed||data.retests?.awaiting);
 $('growth-actions-empty').hidden=has;$('growth-actions-content').hidden=!has;
 const list=$('growth-action-chart');list.replaceChildren();const max=Math.max(1,...data.actions.map(x=>x.count));
 for(const item of data.actions){const row=link('', '/records?'+new URLSearchParams({view:'list',action_id:item.action_id,date_from:data.date_from,date_to:data.date_to}),'growth-action-mini'),label=el('span',item.name),bar=el('span',undefined,'growth-action-mini-track'),fill=el('i');fill.style.width=(item.count/max*100)+'%';bar.append(fill);row.append(label,bar,el('small',item.count+'条'));list.append(row);}
 $('growth-action-distribution').hidden=!data.actions.length;const choices=$('growth-observation-choices');choices.replaceChildren();const tableHost=$('growth-observation-data'),refs=$('growth-observation-links');tableHost.replaceChildren();refs.replaceChildren();
 const empty=$('growth-observation-empty');empty.hidden=Boolean(groups.length);$('growth-observation-chart').hidden=!groups.length;
 if(groups.length){
  chosen=Math.min(chosen,groups.length-1);const field=el('label','检查项','field'),select=el('select');select.setAttribute('aria-label','选择动作检查项');for(const [index,group]of groups.entries())select.append(new Option(group.title+({left:' · 左侧',right:' · 右侧'}[group.side]||''),String(index),false,index===chosen));select.onchange=()=>{chosen=Number(select.value);renderActions();};field.append(select);choices.append(field);
  const group=groups[chosen],records=[...group.records].sort((a,b)=>a.started_at_ms-b.started_at_ms);const comparable=group.comparable_view!==false&&!['unknown','auto'].includes(group.view);
  $('growth-observation-chart').hidden=!comparable;empty.hidden=comparable;empty.textContent=comparable?'':'';
  if(comparable)chart('growth-observation-chart',{tooltip:{trigger:'axis',renderMode:'richText',valueFormatter:value=>value===null?'':value+'%'},grid:{top:18,right:18,bottom:34,left:48},xAxis:{...axis,type:'category',data:records.map((x,index)=>'观察 '+(index+1))},yAxis:{...axis,type:'value',min:0,max:100,axisLabel:{...axis.axisLabel,formatter:'{value}%'}},series:[{name:'同项观察比例',type:'line',connectNulls:false,symbolSize:7,lineStyle:{width:2},data:records.map(x=>x.evaluated?Math.round(x.issue/x.evaluated*1000)/10:null)}]},value=>{if(records[value.dataIndex])navigate('/records/'+records[value.dataIndex].record_id);});
  const table=el('table',undefined,'growth-observation-table'),head=el('tr');for(const label of ['记录','出现提示 / 可评估（'+(group.unit==='ms'?'秒':'次')+'）',''])head.append(el('th',label));const thead=el('thead');thead.append(head);table.append(thead);const body=el('tbody');
  for(const [index,record]of records.entries()){const row=el('tr');row.append(el('td','观察 '+(index+1)),el('td',record.evaluated?(group.unit==='ms'?record.issue/1000:record.issue)+' / '+(group.unit==='ms'?record.evaluated/1000:record.evaluated):''),el('td',''));body.append(row);refs.append(link(new Date(record.started_at_ms).toLocaleDateString('zh-CN')+' · 查看依据','/records/'+record.record_id));}table.append(body);tableHost.append(table);
 }else{empty.textContent='';}
 const retests=$('review-retests');retests.replaceChildren();if(data.retests){const counts=el('p','有效复测 '+data.retests.completed+' 项 · 待复测 '+data.retests.awaiting+' 项','field-help');retests.append(counts,link('查看纠正任务 →','/coach/tasks'));}
}
function renderDiet(){
 const has=Boolean(data.diet_records);$('growth-diet-empty').hidden=has;$('growth-diet-content').hidden=!has;$('growth-food-days').replaceChildren();
 for(const [key,name]of [['staple','主食'],['protein','鱼肉蛋豆'],['vegetable','蔬菜'],['fruit','水果']]){const row=el('div',undefined,'growth-food-row'),track=el('div',undefined,'growth-food-track'),fill=el('span');fill.style.width=(data.diet_days?data.food_days[key]/data.diet_days*100:0)+'%';track.append(fill);row.append(el('span',name),track,el('strong',data.food_days[key]+'天'));$('growth-food-days').append(row);}
 $('growth-diet-note').textContent='本期记录中，蔬菜出现了 '+data.food_days.vegetable+' 天。';$('growth-meals-link').href='/coach/meals?'+new URLSearchParams({date_from:data.date_from,date_to:data.date_to});
}
function renderAll(){renderMetrics();renderTraining();renderActions();renderDiet();}
function selectPanel(name,focus=false){
 activePanel=name;for(const button of page.querySelectorAll('[data-growth-tab]')){const selected=button.dataset.growthTab===name;button.setAttribute('aria-selected',String(selected));button.tabIndex=selected?0:-1;$('growth-panel-'+button.dataset.growthTab).hidden=!selected;if(selected&&focus)button.focus();}
 if(data){renderAll();for(const instance of charts.values())if(instance.getDom().getClientRects().length)instance.resize();}
 const params=new URLSearchParams(location.search);if(name==='training')params.delete('tab');else params.set('tab',name);history.replaceState(null,'',location.pathname+(params.size?'?'+params:''));
}
async function load(){
 if(!facts||disposed)return;const own=++sequence;page.dataset.growthState='loading';page.setAttribute('aria-busy','true');$('growth-error').hidden=true;$('growth-retry').hidden=true;
 try{const result=await api('/api/v1/training/growth?'+new URLSearchParams({week_start:facts.week_start,days}));if(disposed||own!==sequence)return;data=result;$('growth-range').textContent=result.date_from.replaceAll('-','.')+' — '+result.date_to.replaceAll('-','.');
  const select=$('review-action-filter'),old=select.value;select.replaceChildren(new Option('全部动作',''));const actions=new Map(result.actions.map(x=>[x.action_id,x.name]));for(const group of result.trend_groups||[])if(!actions.has(group.action_id))actions.set(group.action_id,group.action_name||group.action_id);for(const [id,name]of actions)select.append(new Option(name,id));if(actions.has(old))select.value=old;
  page.dataset.growthState='ready';page.dataset.growthLoaded='true';page.setAttribute('aria-busy','false');renderAll();
 }catch(error){if(own===sequence&&!disposed)fail(error);}
}
window.addEventListener('fitness-review-loading',()=>{sequence++;page.dataset.growthState='loading';page.setAttribute('aria-busy','true');});
window.addEventListener('fitness-review-failed',()=>{sequence++;page.dataset.growthState='failed';page.setAttribute('aria-busy','false');});
window.addEventListener('fitness-review-loaded',event=>{facts=event.detail;chosen=0;load();});if(window.fitnessReviewFacts){facts=window.fitnessReviewFacts;load();}
$('review-action-filter').onchange=()=>{chosen=0;renderActions();};
for(const button of document.querySelectorAll('[data-growth-days]'))button.onclick=()=>{if(days===button.dataset.growthDays)return;days=button.dataset.growthDays;for(const item of document.querySelectorAll('[data-growth-days]'))item.setAttribute('aria-pressed',String(item===button));window.dispatchEvent(new CustomEvent('fitness-growth-scope-change'));load();};
$('growth-retry').onclick=load;
const resize=new ResizeObserver(()=>{for(const instance of charts.values())if(instance.getDom().getClientRects().length)instance.resize();});resize.observe(page);
$('growth-fullscreen').onclick=async()=>{try{if(document.fullscreenElement)await document.exitFullscreen();else await page.requestFullscreen();}catch(error){$('growth-error').textContent='请使用浏览器全屏功能。';$('growth-error').hidden=false;}};
document.addEventListener('fullscreenchange',()=>{$('growth-fullscreen').querySelector('span').textContent=document.fullscreenElement?'退出全屏':'全屏';});
for(const button of page.querySelectorAll('[data-growth-tab]')){button.onclick=()=>selectPanel(button.dataset.growthTab);button.onkeydown=event=>{const names=['training','actions','diet'],index=names.indexOf(activePanel);let next;if(event.key==='ArrowRight')next=(index+1)%3;else if(event.key==='ArrowLeft')next=(index+2)%3;else if(event.key==='Home')next=0;else if(event.key==='End')next=2;else return;event.preventDefault();selectPanel(names[next],true);};}
const initial=new URLSearchParams(location.search).get('tab');if(['training','actions','diet'].includes(initial))selectPanel(initial);
window.addEventListener('pagehide',()=>{disposed=true;sequence++;resize.disconnect();for(const instance of charts.values())instance.dispose();charts.clear();});window.addEventListener('pageshow',event=>{if(event.persisted){disposed=false;resize.observe(page);load();}});window.addEventListener('fitness-auth-revoked',()=>{disposed=true;sequence++;for(const instance of charts.values())instance.dispose();charts.clear();page.replaceChildren();});
