// 将已保存的判项整理为结论与实际问题，原始记录不改写。
export function feedbackSummary(record){
  const detected=record?.posture?.findings||record?.posture_observations||[];
  const confirmed=record?.confirmed_demo_findings||[];
  const replaced=new Set(confirmed.map(x=>x.code));
  const findings=[...detected.filter(x=>x.status!=='issue'||!replaced.has(x.code)),...confirmed];
  const issues=[];
  for(const item of findings.filter(x=>x.status==='issue').sort((a,b)=>a.start_ms-b.start_ms)){
    const previous=issues.findLast(x=>x.code===item.code&&(x.side||'none')===(item.side||'none')&&x.rule_version===item.rule_version&&x.evidence?.view===item.evidence?.view);
    if(previous&&item.start_ms<=previous.end_ms+250){previous.end_ms=Math.max(previous.end_ms,item.end_ms);continue;}
    issues.push({...item});
  }
  const checked=findings.some(x=>x.status==='not_observed');
  const state=issues.length?'issue':checked?'clear':'unassessable';
  const checks=(record?.supported_items||[]).map(rule=>{
    const values=findings.filter(x=>x.code===rule.code);
    return {code:rule.code,title:rule.title,status:values.some(x=>x.status==='issue')?'issue':values.some(x=>x.status==='not_observed')?'clear':'unassessable'};
  });
  return {state,title:state==='issue'?'有动作需要调整':state==='clear'?'已检查项目未见偏差':'暂未形成动作结论',issues,checks};
}
