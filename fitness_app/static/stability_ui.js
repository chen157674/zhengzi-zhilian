import {api} from './app.js';
import {el,privateError} from './record_common.js';
const sideName={left:'左侧',right:'右侧',both:'双侧'};
const key=item=>[item.group,item.continuity,item.metric,item.side,item.kind].join('|');
function number(value,unit){return Number.isFinite(value)?(unit==='degrees'?value.toFixed(1)+'°':value.toFixed(3)+' 倍'):'—';}
let panel=null;
export function renderStability(data){if(!data.movement_stability||data.movement_stability.status==='not_recorded'){if(panel?.sid===data.session_id)panel.root.hidden=true;return;}if(!panel||panel.sid!==data.session_id)panel=new Panel(data);else panel.update(data);panel.root.hidden=false;}
class Panel{
 constructor(data){
  this.sid=data.session_id;this.root=el('section',undefined,'detail-panel stability-panel');this.root.id='record-stability';this.root.append(el('h2','组内运动观察'));
  this.state=el('p',undefined,'field-help');this.state.setAttribute('role','status');this.cards=el('div',undefined,'stability-cards');this.root.append(this.state,this.cards);
  this.details=el('details');this.details.append(el('summary','动作变化曲线'));const label=el('label','选择观测项目');this.select=el('select');this.select.setAttribute('aria-label','稳定性观测项目');label.append(this.select);this.canvas=el('canvas');this.canvas.setAttribute('aria-label','已保存区间的投影测量值；断点表示未知或条件改变');this.chartState=el('p',undefined,'field-help');this.details.append(label,this.canvas,this.chartState);this.root.append(this.details);

  document.querySelector('#record-retest').after(this.root);this.details.addEventListener('toggle',()=>{if(this.details.open)this.load();});this.select.onchange=()=>this.draw();
  this.observer=new ResizeObserver(()=>this.draw());this.observer.observe(this.root);window.addEventListener('pagehide',()=>{this.disposed=true;this.windows=[];this.observer.disconnect();},{once:true});this.update(data);
 }
 update(data){
  const summary=data.movement_stability||{status:'not_recorded',message:'本记录没有运动摘要，不自动重新分析。',comparisons:[]};const version=[summary.state_version,summary.status,data.status].join(':');if(this.version===version)return;this.version=version;this.data=data;this.summary=summary;
  this.state.textContent=summary.status==='active'?'正在记录动作变化…':['partial','limited','interrupted'].includes(summary.status)?'仅记录了部分动作。':'';
  const values=[...(summary.comparisons||[])].sort((a,b)=>(a.state!=='compared')-(b.state!=='compared')||(a.direction==='no_clear_change')-(b.direction==='no_clear_change')||(a.side!=='both')-(b.side!=='both'));
  const selected=this.select.value;this.select.replaceChildren();this.cards.replaceChildren();const extra=el('details'),extraBody=el('div');extra.append(el('summary','其余 '+Math.max(0,values.length-3)+' 项观察'),extraBody);
  const ordinal=new Map();for(const value of values){const cohort=value.group+'|'+value.continuity;if(!ordinal.has(cohort))ordinal.set(cohort,[...ordinal.keys()].filter(k=>k.startsWith(value.group+'|')).length+1);const text=`第${value.group}组 · 观察段${ordinal.get(cohort)} · ${value.title} · ${sideName[value.side]||'所选侧别'}`;this.select.append(new Option(text,key(value)));const card=this.card(value,text);(this.cards.childElementCount<3?this.cards:extraBody).append(card);}
  if(extraBody.childElementCount)this.cards.append(extra);if(!values.length&&summary.status!=='not_recorded')this.cards.append(el('p','请补充同项动作记录后对比。','muted'));
  if([...this.select.options].some(v=>v.value===selected))this.select.value=selected;this.details.hidden=!summary.window_count;this.windows=null;if(this.details.open)this.load();
 }
 card(item,title){const card=el('article',undefined,'stability-card');card.append(el('h3',item.title+(item.side==='both'?'':' · '+(sideName[item.side]||''))),el('p',title.split(' · ').slice(0,2).join(' · '),'field-help'),el('p',item.state==='compared'?(item.direction==='increase'?'后半段增大。':item.direction==='decrease'?'后半段减小。':'前后没有明确变化。'):item.message));if(item.state!=='compared')return card;
  const pair=el('div',undefined,'stability-pair');for(const [key,label]of [['before','前半段'],['after','后半段']]){const values=item[key],cell=el('div');cell.append(el('span',label,'field-help'),el('strong',number(values.median,item.unit)),el('p',`可比 ${values.samples} · 未知 ${values.unknown}`,'field-help'));pair.append(cell);}card.append(pair);
  return card;
 }
 async load(){if(this.disposed||this.loading||this.windows)return;this.loading=true;const epoch=this.requestEpoch=(this.requestEpoch||0)+1,version=this.version;this.chartState.textContent='正在读取动作变化…';try{const value=await api('/api/v1/records/'+this.sid+'/stability?windows=1');if(this.disposed)return;if(this.version!==version){this.loading=false;this.load();return;}this.windows=value.windows||[];this.draw();}catch(error){if(!privateError(error))this.chartState.textContent=error.message;}finally{if(this.requestEpoch===epoch)this.loading=false;}}
 draw(){if(!this.details.open||!this.windows)return;const item=(this.summary.comparisons||[]).find(v=>key(v)===this.select.value);if(!item)return;
  const rows=this.windows.filter(w=>w.group===item.group&&w.continuity===item.continuity&&w.kind===item.kind).map(w=>({window:w,metric:w.metrics.find(m=>m.metric===item.metric&&m.side===item.side)})).filter(v=>v.metric);
  const valid=rows.filter(v=>v.metric.status==='observed'&&Number.isFinite(v.metric.value));this.chartState.textContent=valid.length?'':'此处没有可绘制的有效测量值。';
  const width=Math.max(240,this.canvas.clientWidth||this.root.clientWidth),height=220,dpr=Math.min(devicePixelRatio||1,2);this.canvas.width=width*dpr;this.canvas.height=height*dpr;const ctx=this.canvas.getContext('2d');ctx.scale(dpr,dpr);ctx.clearRect(0,0,width,height);if(!rows.length)return;
  const max=Math.max(.01,...valid.map(v=>v.metric.value)),start=rows[0].window.start_ms,end=rows.at(-1).window.end_ms,pad=28;ctx.strokeStyle='#d7dfe3';ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(pad,pad);ctx.lineTo(pad,height-pad);ctx.lineTo(width-pad,height-pad);ctx.stroke();ctx.fillStyle='#667386';ctx.font='12px sans-serif';ctx.fillText('0',10,height-pad);ctx.fillText(max.toFixed(item.unit==='degrees'?1:3),2,15);let previous=null;
  for(const value of rows){const w=value.window,m=value.metric,x=pad+((w.start_ms+w.end_ms)/2-start)/Math.max(1,end-start)*(width-2*pad);if(m.status!=='observed'||!Number.isFinite(m.value)){previous=null;ctx.fillStyle='#8794a4';ctx.fillText('?',x-3,height-pad-5);continue;}const y=height-pad-m.value/max*(height-2*pad);ctx.strokeStyle='#298f83';ctx.fillStyle='#298f83';ctx.lineWidth=2;
   if(previous&&w.start_ms-previous.window.end_ms<=500&&w.view===previous.window.view&&w.view_basis===previous.window.view_basis&&Math.max(m.scale,previous.metric.scale)/Math.min(m.scale,previous.metric.scale)<=1.25){ctx.beginPath();ctx.moveTo(previous.x,previous.y);ctx.lineTo(x,y);ctx.stroke();}ctx.beginPath();ctx.arc(x,y,3,0,Math.PI*2);ctx.fill();previous={...value,x,y};}
 }
}
