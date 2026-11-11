import {api,confirmDialog} from './app.js';
import {el} from './record_common.js';
const names={front:'正面',side:'侧面',oblique:'斜侧面'},sides={left:'左侧',right:'右侧',none:'所选判项'};
function destination(option){return '/realtime?'+new URLSearchParams({retest_record:option.origin.session_id,retest_evidence:option.origin.evidence_id});}
export async function renderRecordRetest(data){
 const host=document.querySelector('#record-retest');if(!host)return;
 const key=data.session_id+':'+data.state_version;if(host.dataset.key===key)return;host.dataset.key=key;host.hidden=false;host.replaceChildren();
 host.append(el('h2',data.retest?'调整后复测':'验证一项调整'));
 if(data.retest){const r=data.retest;host.append(el('p',r.message));if(r.title)host.append(el('p',r.title,'field-help'));if(r.observation){const o=r.observation;host.append(el('p',o.unit==='cycles'?`同项可评估 ${o.evaluated} 次，无法评估 ${o.unknown} 次。`:`同项有效观察 ${(o.evaluated/1000).toFixed(1)} 秒；未知区间不算正常。`,'field-help'));}}
 try{const value=await api('/api/v1/records/'+data.session_id+'/retest-options');if(host.dataset.key!==key)return;
  if(!value.options.length){if(!data.retest)host.hidden=true;return;}
  host.append(el('p','只复测你选中的一项提示；自动识别动作后，主动结束并查看结果。','field-help'));
  for(const option of value.options){const row=el('div',undefined,'retest-option');const text=el('div');text.append(el('strong',option.title),el('p',`${sides[option.side]||'所选侧别'} · 原机位：${names[option.view]} · 姿态参考`,'field-help'));const link=el('a','验证这项调整','button secondary');link.href=destination(option);link.dataset.nav='';const actions=el('div',undefined,'button-row');actions.append(link);if(['machine_chest_press','standing_dumbbell_curl','bodyweight_squat'].includes(option.action_id)){const save=el('button','保存纠正任务','button secondary small');save.type='button';let pending=null;save.onclick=async()=>{if(!pending&&!await confirmDialog({title:'保存这一项纠正任务',message:option.title+'。保留原提示来源，练习后需新记录复测。',confirmLabel:'确认保存'}))return;save.disabled=true;try{pending??={id:crypto.randomUUID(),body:{origin:option.origin,confirmed:true,practice:{steps:option.suggestion?[{text:option.suggestion,citation_ids:[]}]:[],knowledge_refs:[]}}};await api('/api/v1/retest-tasks',{method:'POST',commandId:pending.id,body:pending.body});pending=null;save.hidden=true;const tasks=el('a','查看纠正任务 →','text-link');tasks.href='/coach/tasks';tasks.dataset.nav='';actions.append(tasks);window.dispatchEvent(new Event('fitness-retest-saved'));}catch(error){save.textContent=error.unknown?'核对原保存':'重试保存任务';if(!error.unknown)pending=null;text.append(el('p',error.message,'notice error'));}finally{save.disabled=false;}};actions.append(save);}row.append(text,actions);host.append(row);}
 }catch(error){if(host.dataset.key===key)host.append(el('p',error.message,'notice error'));}
}
export class LiveRetest{
 constructor(){this.option=null;this.host=document.querySelector('#live-retest-panel');this.ready=this.load();}
 async load(){const query=new URLSearchParams(location.search),sid=query.get('retest_record'),eid=query.get('retest_evidence');if(!sid&&!eid)return;
  this.host.hidden=false;this.host.replaceChildren(el('p','正在读取原提示…'));
  try{if(!sid||!eid)throw new Error('复测来源不完整，请从记录中的提示重新进入。');const data=await api('/api/v1/records/'+encodeURIComponent(sid)+'/retest-options');this.option=data.options.find(v=>v.origin.evidence_id===eid);if(!this.option)throw new Error('原提示已更新或不适合复测，请重新选择。');this.host.replaceChildren(el('strong','验证这项调整'),el('p',this.option.title),el('p',`${this.option.action_name} · 原机位：${names[this.option.view]}。仍自动识别动作；请主动开启摄像头，结束后查看同项参考结果。`,'field-help'));this.state=el('p','尚未开始采集；此处不会自动申请相机权限。','field-help');this.host.append(this.state);
  }catch(error){this.error=error;this.host.replaceChildren(el('p',error.message,'notice error'));}
  const exit=el('a','返回普通训练','text-link');exit.href='/realtime';exit.dataset.nav='';this.host.append(exit);
 }
 async payload(){await this.ready;if(this.error)throw this.error;return this.option?{retest_origin:this.option.origin}:{};}
 update(result){if(!this.option||!this.state)return;const action=result.automatic_analysis?.action_id||result.recognition?.action_id;this.state.textContent=action&&action!==this.option.action_id?'当前识别动作与复测目标不同，本段不会判为调整成功。':'正在采集新观察；结束后只比较所选项目，观察不足会明确说明。';}
}
