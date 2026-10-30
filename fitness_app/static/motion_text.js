const phases={seeking_start:'等待起点',ready:'起点已确认',departing:'开始移动',moving:'运动中',returning:'正在返回',confirming_return:'确认返回',unobservable:'关键部位不可见'};
Object.assign(phases,{seeking_support:'等待前臂支撑',confirming_support:'正在确认前臂支撑',holding:'前臂支撑已确认，累计保持时间'});
const reasons={start_posture_unconfirmed:'请回到所选动作的起点',return_posture_unconfirmed:'未回到所选动作起点，本次未计数',insufficient_correlated_motion:'相关部位运动证据不足，本次未计数',cycle_too_long:'本次运动未能确认完整周期，请回到起点',required_parts_missing:'关键部位不可见，等待重新确认',prepare_waiting_for_start:'请在倒计时结束前保持起点'};
Object.assign(reasons,{backward_direction_unconfirmed:'请站回起点，并让双腿、足跟与脚尖可见',backward_step_unconfirmed:'未能确认后撤，请回到起点',waiting_for_backward_step:'等待确认后撤腿',support_foot_moved:'支撑脚位置改变，本次未能确认后撤周期',side_identity_ambiguous:'双腿侧别不明确，请调整机位并回到起点'});
Object.assign(reasons,{forearm_support_unconfirmed:'未能确认前臂支撑，暂不累计保持时间'});
Object.assign(reasons,{jack_sampling_gap:'采样间隔过大，本次无法确认完整开合',jack_front_view_unconfirmed:'请正面全身入镜，保持手脚可见',jack_closed_start_unconfirmed:'请先让手臂和双脚回到合拢起点',jack_joint_open_unconfirmed:'未确认手脚共同张开，本次未计数'});
export function motionText(analysis){
  if(!analysis?.sides)return '';
  return Object.entries(analysis.sides).map(([side,state])=>`${side==='left'?'左侧 · ':side==='right'?'右侧 · ':''}${reasons[state.reason]||phases[state.phase]||'等待可用画面'}`).join('；');
}
