export function predictionTiming(predictions,timing){
  const values=(predictions||[]).map(item=>item.prediction_ms).filter(value=>typeof value==='number'&&Number.isFinite(value));
  if(!values.length)return {text:'不可计算',note:'没有有效计时记录'};
  const mean=values.reduce((sum,value)=>sum+value,0)/values.length,resolution=timing?.resolution_ms;
  if(typeof resolution!=='number'||!Number.isFinite(resolution)||resolution<=0)return {text:'计时信息不足',note:'未记录可靠的计时精度'};
  if(mean<resolution)return {text:'计时精度不足',note:`原时钟分辨率 ${resolution.toFixed(3)} ms`};
  return {text:mean.toFixed(3)+' ms',note:`原测试均值；分辨率 ${resolution.toFixed(3)} ms`};
}
