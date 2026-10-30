// 展示层只缩短文案，不把无结果当作正常。
export function coachingCue(coaching){
  const issue=coaching?.primary_cue;
  if(issue)return {state:'issue',title:issue.message||'需要调整',instruction:issue.suggestion||''};
  const state=coaching?.state;
  if(state==='observing')return {state:'unassessable',title:'正在观察',instruction:''};
  const labels={no_person:'请站入画面',multiple_people:'请保持单人入镜',positioning:'请完整入镜',evidence_insufficient:'分析中',waiting_for_input:'等待画面',paused:'已暂停',resting:'组间休息',ended:'分析结束',save_failed:'结果尚未保存',interrupted:'分析中断'};
  return {state:'unassessable',title:labels[state]||'分析中',instruction:''};
}

export function recordCue(record,stamp){
  if(!record||!Number.isFinite(stamp))return {state:'unassessable',title:'等待分析',instruction:''};
  const findings=record.posture?.findings||record.posture_observations||[];
  const active=findings.filter(x=>Number.isFinite(x.start_ms)&&Number.isFinite(x.end_ms)&&x.start_ms<=stamp&&stamp<x.end_ms);
  const issue=active.find(x=>x.status==='issue');
  if(issue)return {state:'issue',title:issue.message||'需要调整',instruction:issue.suggestion||''};
  if(active.some(x=>x.status==='not_observed'))return {state:'clear',title:'已检查项目未见偏差',instruction:''};
  return {state:'unassessable',title:'该片段暂无结论',instruction:''};
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
  panel.hidden=false;panel.dataset.state=cue.state;
  const title=panel.querySelector('[data-cue-title]'),text=panel.querySelector('[data-cue-text]');
  if(title.textContent!==cue.title)title.textContent=cue.title;
  if(text.textContent!==cue.instruction)text.textContent=cue.instruction;
  text.hidden=!cue.instruction;
}
