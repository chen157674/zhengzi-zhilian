// 只为抽样选择实际问题／完整动作区间，不凭画面补造检测结论。
export function reviewSampleTimes(record,startMs=0,endMs=Infinity,count=6){
  const valid=value=>Number.isFinite(value.start_ms)&&Number.isFinite(value.end_ms)&&value.end_ms>value.start_ms&&value.start_ms<endMs&&value.end_ms>startMs;
  const findings=[...(record?.posture?.findings||record?.posture_observations||[]),...(record?.confirmed_demo_findings||[])];
  const issues=findings.filter(value=>value.status==='issue'&&valid(value)).sort((a,b)=>(b.end_ms-b.start_ms)-(a.end_ms-a.start_ms));
  const cycles=(record?.events||[]).filter(valid);
  const selected=issues[0]||cycles[Math.floor(cycles.length/2)];
  if(!selected)return [];
  const center=Math.min(endMs,Math.max(startMs,(selected.start_ms+selected.end_ms)/2));
  const half=Math.max(600,Math.min(2000,(selected.end_ms-selected.start_ms)/2));
  const left=Math.max(startMs,center-half),right=Math.min(endMs,center+half);
  return Array.from({length:count},(_,index)=>left+(right-left)*(index+.5)/count);
}
