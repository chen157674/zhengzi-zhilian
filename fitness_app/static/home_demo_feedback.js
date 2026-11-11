// 只展示同帧有效结论；缺失证据不会显示正常。
const states=new Set(['clear','issue','unassessable','observing']);
const statuses=new Set(['issue','not_observed','unassessable']);
const previous=new WeakMap();
export function validFeedback(data){
  if(!data||data.schema_version!==1||!states.has(data.state)||typeof data.title!=='string'||typeof data.description!=='string'||typeof data.instruction!=='string'||typeof data.scope_note!=='string'||!Array.isArray(data.checks)||data.checks.length>100)return false;
  if(!data.checks.every(item=>item&&typeof item.code==='string'&&typeof item.label==='string'&&typeof item.detail==='string'&&statuses.has(item.status)))return false;
  if(data.state==='clear'&&(!data.checks.some(item=>item.status==='not_observed')||data.checks.some(item=>item.status==='issue')))return false;
  if(data.clear_label!==undefined&&!['已检查项目未见偏差','你标记的问题尚未检出'].includes(data.clear_label))return false;
  if(data.state==='issue'&&(!data.instruction.trim()||!data.checks.some(item=>item.status==='issue')))return false;
  if(data.highlight!=null){
    if(data.state!=='issue'||!Array.isArray(data.highlight.joint_indices)||!data.highlight.joint_indices.every(i=>Number.isInteger(i)&&i>=0&&i<33))return false;
  }
  return true;
}
export function renderFeedback(panel,data){
  if(!panel)return;
  const value=validFeedback(data)?data:{state:'unassessable'};
  const title={clear:value.clear_label||'动作正常',observing:'正在分析',unassessable:'正在分析'}[value.state]||value.title;
  const instruction=value.state==='issue'?value.instruction:'';
  const shownState=value.state==='clear'&&value.clear_label==='你标记的问题尚未检出'?'unassessable':value.state;
  const signature=JSON.stringify([shownState,title,instruction]);
  if(previous.get(panel)===signature)return;
  previous.set(panel,signature);
  panel.dataset.state=shownState;
  panel.setAttribute('aria-busy',String(shownState==='observing'));
  panel.querySelector('[data-feedback-title]').textContent=title;
  const text=panel.querySelector('[data-feedback-instruction]');
  text.textContent=instruction;text.hidden=!instruction;
  panel.querySelector('.feedback-mark').textContent={clear:'✓',issue:'!',unassessable:'·',observing:'·'}[shownState];
}
