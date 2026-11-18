// 展示层只缩短文案，不把无结果当作正常。
export function coachingCue(coaching){
  const state=coaching?.state;
  const withdrawn=new Set(['preparing','paused','resting','ended','save_failed','interrupted','waiting_for_input','no_person','multiple_people','positioning','evidence_insufficient']);
  const issue=withdrawn.has(state)?null:coaching?.primary_cue;
  if(issue)return {state:'issue',title:issue.message||'需要调整',instruction:issue.suggestion||''};
  if(state==='evidence_insufficient'||state==='observing'&&coaching?.has_unassessable_items)return {state:'unassessable',title:'',instruction:''};
  if(state==='observing')return {state:'unassessable',title:'正在观察',instruction:''};
  const labels={preparing:'准备就绪',no_person:'请站入画面',multiple_people:'请保持单人入镜',positioning:'请完整入镜',evidence_insufficient:'分析中',waiting_for_input:'等待画面',paused:'已暂停',resting:'组间休息',ended:'分析结束',save_failed:'结果待保存',interrupted:'分析中断'};
  return {state:'unassessable',title:labels[state]||'分析中',instruction:coaching?.instruction||''};
}

export function recordCue(record,stamp){
  if(!record||!Number.isFinite(stamp))return {state:'unassessable',title:'等待分析',instruction:''};
  const confirmed=(record.confirmed_demo_findings||[]).find(x=>x.status==='issue'&&x.start_ms<=stamp&&stamp<x.end_ms);
  if(confirmed)return {state:'issue',title:confirmed.message,instruction:confirmed.suggestion||''};
  const findings=record.posture?.findings||record.posture_observations||[];
  const active=findings.filter(x=>Number.isFinite(x.start_ms)&&Number.isFinite(x.end_ms)&&x.start_ms<=stamp&&stamp<x.end_ms);
  const issue=active.find(x=>x.status==='issue');
  if(issue)return {state:'issue',title:issue.message||'需要调整',instruction:issue.suggestion||''};
  if(findings.some(x=>x.status==='issue'))return {state:'unassessable',title:'',instruction:''};
  if(active.some(x=>x.status==='not_observed'))return {state:'clear',title:'检查项通过',instruction:''};
  return {state:'unassessable',title:'',instruction:''};
}

// 分析过程保持观察状态；完成后仅回放已保存的真实时间段结论。
export function videoCue(job,record,stamp){
  if(!job)return {state:'unassessable',title:'等待分析',instruction:''};
  if(job.save_state&&job.save_state!=='clean')return coachingCue({state:'save_failed'});
  if(!job.stopped){
    if(['paused','resting','save_failed','interrupted'].includes(job.coaching?.state))return coachingCue(job.coaching);
    return {state:'observing',title:'正在观察',instruction:''};
  }
  return recordCue(record,stamp);
}

export function paintCue(panel,cue){
  if(!panel)return;
  panel.dataset.state=cue.state;panel.hidden=!cue.title&&!cue.instruction;if(panel.hidden)return;
  const title=panel.querySelector('[data-cue-title]'),text=panel.querySelector('[data-cue-text]');
  if(title.textContent!==cue.title)title.textContent=cue.title;
  if(text.textContent!==cue.instruction)text.textContent=cue.instruction;
  if(text.hidden!==!cue.instruction)text.hidden=!cue.instruction;
  const label=panel.querySelector('[data-cue-label]'),marker=panel.querySelector('[data-cue-marker]'),next=panel.querySelector('[data-cue-next]');
  const issue=cue.state==='issue';
  if(label){const value=issue?'动作纠正提示':cue.state==='observing'?'分析状态':'片段反馈';if(label.textContent!==value)label.textContent=value;}
  if(marker){const value=issue?'!':cue.state==='clear'?'✓':cue.state==='observing'?'…':'·';if(marker.textContent!==value)marker.textContent=value;}
  if(next){const hidden=!issue||!cue.instruction;if(next.hidden!==hidden)next.hidden=hidden;}
}

// 只格式化正式来源已有字段，未知部位、侧别和时间不猜测。
export function cueDetails(issue){
  if(!issue)return {body:'',time:'',version:''};
  const parts=Array.isArray(issue.body_parts)?issue.body_parts.filter(x=>typeof x==='string'):[];
  const side={left:'左侧',right:'右侧',both:'双侧'}[issue.side]||'';
  const stamp=value=>{const seconds=Math.floor(value/1000);return String(Math.floor(seconds/60)).padStart(2,'0')+':'+String(seconds%60).padStart(2,'0');};
  const valid=Number.isFinite(issue.start_ms)&&Number.isFinite(issue.end_ms)&&issue.start_ms>=0&&issue.end_ms>issue.start_ms;
  return {body:[side,...parts].filter(Boolean).join(' · '),time:valid?stamp(issue.start_ms)+'—'+stamp(issue.end_ms):'',version:typeof issue.rule_version==='string'?issue.rule_version:''};
}
