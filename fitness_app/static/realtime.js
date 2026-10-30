import {api,confirmDialog,showMessage,RequestError} from './app.js';
import {cameraControl} from './camera.js';
import {terminal,statusNames,duration,dateFormat,privateError} from './record_common.js';
import {targetForm} from './target_form.js';
import {motionText} from './motion_text.js';
import {createVoiceFeedback} from './voice_feedback.js';

const root=document.querySelector('#camera-preparation'),canStart=root.dataset.canStart==='1';
const start=document.querySelector('#realtime-start'),errorBox=document.querySelector('#live-error');
const pause=document.querySelector('#live-pause'),end=document.querySelector('#live-end'),resume=document.querySelector('#live-resume'),claim=document.querySelector('#live-claim');
const cancelCountdown=document.querySelector('#live-cancel-countdown'),banner=document.querySelector('#live-save-failure');
const target=targetForm(root),extendRest=document.querySelector('#live-extend-rest');
const voice=createVoiceFeedback(root.dataset.actionId);
let restoredConfig=false,restQuote=null,displayTimer=null;
let sid=root.dataset.sessionId||'',control=null,lastControl=null,current=null,busy=false,pollTimer=null,heartbeatTimer=null,disposed=false,stopped=false;
const pendingKey='fitness-create-'+document.body.dataset.userId;
let pendingCreate=null,pendingReadFailed=false;
try{const saved=sessionStorage.getItem(pendingKey);if(saved){const value=JSON.parse(saved);if(typeof value.commandId!=='string'||!value.body||value.body.source!=='camera')throw new Error('invalid pending marker');pendingCreate=value}}catch{pendingReadFailed=true;errorBox.textContent='无法读取本页待核对请求；确认存储可用并核对原记录后再开始。';errorBox.hidden=false}
function clearPending(){pendingCreate=null;try{sessionStorage.removeItem(pendingKey)}catch{showMessage('原请求已确认，但本页请求标识未能清除；刷新后可再次核对原记录。')}}
function error(error){voice.suspend();if(privateError(error)){cameraControl.suspend();return}if(error.fields)target.paint(error.fields,true);errorBox.textContent=error.message||String(error);if(error.fields?.session_id){const link=document.createElement('a');link.href='/realtime/'+encodeURIComponent(error.fields.session_id);link.textContent='返回当前分析';link.className='text-link';errorBox.append(' ',link)}errorBox.hidden=false}
function owns(){return Boolean(control&&current?.control.has_controller&&control.controller_epoch===current.control.controller_epoch)}
function credentials(){if(!owns())throw new Error('当前页只读，请先取得或接管控制。');return {control_token:control.control_token,controller_epoch:control.controller_epoch,stream_epoch:current.control.stream_epoch}}
function payload(command,extra={}){return {command,expected_version:current.state_version,...credentials(),...extra}}
function cleanupPayload(command,extra={}){if(!lastControl)throw new Error('本页没有原控制关系，请本人重新认证核对。');return {command,expected_version:current.state_version,...lastControl,...extra}}
function remainingRest(){return current?.status==='resting'&&!current.analysis_frozen&&restQuote?Math.max(0,restQuote.remaining-(performance.now()-restQuote.at)):current?.live_rest_remaining_ms??current?.rest_remaining_ms??0}
function renderRest(){
  if(!current)return;
  const resting=current.status==='resting',pausedRest=current.status==='paused'&&current.resume_from==='resting';
  const remaining=remainingRest(),node=document.querySelector('#live-rest');node.hidden=!resting&&!pausedRest;
  node.textContent=`${current.analysis_frozen?'休息已冻结':pausedRest?'休息已暂停':'组间休息'} · 剩余 ${Math.ceil(remaining/1000)} 秒`;
  extendRest.hidden=!resting;extendRest.disabled=busy||!owns()||current.save_state!=='clean'||remaining<=0||remaining+30000>3600000;
}
function render(){
  const live=Boolean(sid&&(!current||current.status!=='ready')),state=current?.status;
  document.querySelector('#prepare-settings').hidden=live;document.querySelector('#live-panel').hidden=!live&&(!sid||state==='ready');
  document.querySelector('#prepare-controls').hidden=live;document.querySelector('#live-controls').hidden=!live;
  document.querySelector('#training-title').textContent=live?'实时分析':'训练准备';
  document.querySelector('#training-subtitle').textContent=live?(owns()?'本页正在控制本次分析。':'当前页只读，不会自动开启摄像头或取得控制。'):'先检查取景，再开始分析。';
  start.disabled=busy||pendingReadFailed||(!pendingCreate&&(!canStart||!cameraControl.current()?.ready));start.textContent=pendingCreate?'核对原创建':'开始分析';
  document.querySelector('#camera-start-reason').textContent=canStart?'准备预览不计次数，不生成训练记录。':'动作分析能力尚未开放；准备预览不计数。';
  if(!current)return;
  voice.update(current,owns(),busy||stopped);
  const own=owns(),ended=terminal.has(state),failed=current.save_state==='save_failed';
  const sk=current.skeleton_save;document.querySelector('#session-skeleton-state').textContent=sk?.status==='limited'?'骨架达到上限，训练摘要继续保存。':sk?.status==='save_failed'?'骨架未完整保存，训练摘要按原流程保存。':'';document.querySelector('#session-save-skeleton').disabled=Boolean(sid);document.querySelector('#session-save-skeleton').checked=current.config.save_skeleton===true;
  const endedMessage=current.save_state!=='clean'?'本次分析已结束，请先核对保存状态。':current.started_at_ms===null?'本次分析已结束，未生成训练记录。':state==='completed'?'本次训练已完成，结果已保存。':'本次分析已结束，已确认的结果已保存。';
  document.querySelector('#live-state').textContent=failed?'分析已冻结':statusNames[state]||state;
  if(failed){document.querySelector('#camera-message').textContent='摄像头采集已停止；请先处理保存状态。';document.querySelector('#camera-empty h2').textContent='摄像头已关闭';document.querySelector('#camera-empty p').textContent=ended?'请先处理保存状态，再核对已保存摘要。':'保存恢复后，需明确继续并重新检查取景。'}
  else if(ended){document.querySelector('#training-subtitle').textContent=endedMessage;document.querySelector('#camera-message').textContent=endedMessage;document.querySelector('#camera-empty h2').textContent='本次分析已结束';document.querySelector('#camera-empty p').textContent=current.started_at_ms===null?'重新训练请返回训练准备页。':'可以查看已保存摘要，或返回训练准备页开始新训练。';document.querySelector('#preview-model-status').textContent='本次分析已结束。'}
  const goal=current.config.target;
  const group=goal&&!ended?(state==='resting'||(state==='paused'&&current.resume_from==='resting')?current.groups.find(group=>group.set_index===current.completed_sets):current.groups.find(group=>['active','pending'].includes(group.status))):null;
  const shown=group||current;
  document.querySelector('#live-mode').textContent=goal?'目标训练':'自由训练';
  document.querySelector('#live-amount-label').textContent=group?`第 ${group.set_index} 组完成`:'累计完成';
  const goalNode=document.querySelector('#live-goal');goalNode.hidden=!goal;
  if(goal)goalNode.textContent=`已完成 ${current.completed_sets} / ${goal.sets} 组 · 每组目标 ${goal.value} ${goal.unit==='seconds'?'秒':goal.unit==='per_side_reps'?'次／侧':'次'}`;
  document.querySelector('#live-amount').textContent=current.counting_available?(current.unit==='seconds'?(shown.hold_ms/1000).toFixed(1):String(shown.total_count)):'—';
  document.querySelector('#live-unit').textContent=current.counting_available?(current.unit==='seconds'?'秒':'次'):'计数能力尚未开放';
  document.querySelector('#live-sides').textContent=current.unit==='per_side_reps'||current.action_id==='high_knees'?`左 ${shown.left_count} 次 · 右 ${shown.right_count} 次`:'';
  document.querySelector('#live-active').textContent=duration(current.live_active_ms??current.active_ms);
  const countdown=document.querySelector('#live-countdown');countdown.hidden=state!=='countdown';countdown.textContent=`准备开始 · ${Math.max(1,Math.ceil((current.countdown_remaining_ms||0)/1000))}`;
  document.querySelector('#live-feedback').textContent=failed?'新增分析与反馈已停止，请先处理保存。':ended?endedMessage:stopped?'本页已停止采集，正在核对保存状态。':current.coaching?.primary_cue?.suggestion||current.coaching?.instruction||voice.problemText(current)||motionText(current.latest_frame?.analysis)||current.latest_frame?.framing.message||(current.unit==='seconds'?'等待可确认的前臂支撑。':'等待可观察的完整周期。');
  document.querySelector('#live-controller-note').textContent=ended?'本次分析不能继续；新训练请从准备页开始。':own?'':'本页只读；取得控制后仍需点击继续并检查取景。';
  claim.hidden=own||ended;claim.disabled=busy;claim.textContent=current.control.has_controller?'接管当前训练':'取得本页控制';
  const reconnect=['running','waiting'].includes(state)&&!cameraControl.isOpen();
  resume.hidden=ended||(!['paused','ready'].includes(state)&&!reconnect);resume.textContent=reconnect?'重新检查取景':state==='ready'?'开始分析':current.resume_from==='resting'&&current.rest_remaining_ms>0?'继续休息':'继续分析';resume.disabled=busy||!own||!canStart||failed;
  pause.disabled=busy||!own||failed||!['running','waiting','resting'].includes(state);
  end.disabled=busy||!own||ended;cancelCountdown.hidden=state!=='countdown';cancelCountdown.disabled=busy||!own;
  const result=document.querySelector('#live-result');result.hidden=!ended||failed||current.started_at_ms===null;result.href='/records/'+sid;
  banner.hidden=!failed;
  if(failed)document.querySelector('#live-save-description').textContent=`最后确认保存 ${dateFormat.format(current.last_saved_at_ms)}；其后尚未写入的动作或时间不会计作已保存成果。`;
  document.querySelector('#live-retry-save').disabled=busy;document.querySelector('#live-discard').disabled=busy||!lastControl;
  renderRest();
}
function apply(data){
  if(sid&&data.session_id!==sid)return;
  if(current&&data.session_id===current.session_id){
    if(data.state_version<current.state_version)return;
    if(data.state_version===current.state_version&&data.latest_frame&&current.latest_frame&&data.latest_frame.frame_seq<current.latest_frame.frame_seq)data={...data,latest_frame:current.latest_frame};
  }
  if(!restoredConfig){target.restore(data.config);restoredConfig=true}
  if(data.status==='resting'&&!data.analysis_frozen){
    const remaining=data.live_rest_remaining_ms??data.rest_remaining_ms;
    if(!restQuote||!current||data.state_version!==current.state_version||remaining<=remainingRest())restQuote={remaining,at:performance.now()};
  }else restQuote=null;
  current=data;
  if(control&&current.control?.has_controller&&control.controller_epoch===current.control.controller_epoch)lastControl={...control,stream_epoch:current.control.stream_epoch};
  if(control&&current.control?.has_controller&&control.controller_epoch!==current.control.controller_epoch){control=null;cameraControl.suspend();showMessage('控制已转移，当前页转为只读。')}
  if(control&&current.control&&!current.control.has_controller){control=null;cameraControl.suspend()}
  if(!busy&&(terminal.has(data.status)||data.status==='paused'||data.analysis_frozen)&&cameraControl.isOpen())cameraControl.suspend();
  render();
}
async function refresh(){
  if(!sid||disposed)return;
  const data=await api('/api/v1/sessions/'+sid);if(disposed)return;apply(data);return data;
}
async function acquire(){
  await refresh();
  const take=Boolean(current.control.has_controller);
  const perform=async()=>{
    const result=await api(`/api/v1/sessions/${sid}/control`,{method:'POST',commandId:crypto.randomUUID(),body:{page_id:cameraControl.pageId,takeover:take,expected_version:current.state_version}});
    if(!result.credential_available||!result.control_token)throw new Error('连接已恢复，请点击继续训练。');
    control={control_token:result.control_token,controller_epoch:result.controller_epoch};lastControl={...control,stream_epoch:result.stream_epoch};await cameraControl.csrfToken();await refresh();return true;
  };
  return take?confirmDialog({title:'接管当前训练',message:'原页面会暂停并失去控制，已保存摘要保留。接管后须重新取景并点击继续，不会自动开始。',confirmLabel:'接管训练',onConfirm:perform}):perform();
}
async function runBusy(task){if(busy)return;busy=true;render();errorBox.hidden=true;try{return await task()}catch(caught){error(caught);try{await refresh()}catch{/* 保留原错误与上次状态。 */}}finally{busy=false;render()}}
async function recoverCreate(){
  const pending=pendingCreate;if(!pending)return;
  let receipt;
  try{receipt=await api('/api/v1/operations/'+pending.commandId)}
  catch(caught){
    if(caught.status!==404)throw caught;
    const retried=await confirmDialog({title:'核对原创建',message:'暂未查到训练，请稍后重试。',confirmLabel:'重试原创建',onConfirm:async()=>{
      try{return await api('/api/v1/sessions',{method:'POST',commandId:pending.commandId,body:pending.body})}
      catch(error){if(!error.unknown)clearPending();if(error.code==='STALE_CAPABILITY')error.message='动作设置已更新，请取消此窗口后重新选择动作。';throw error}
    }});
    if(!retried)return;
    receipt={status:'succeeded',result:{session_id:retried.session_id}};
  }
  try{
    const recovered=receipt.status==='succeeded'&&(receipt.result?.session_id||receipt.result?.id);
    if(!recovered)throw new Error('正在确认上次训练，请稍候。');
    const snapshot=await api('/api/v1/sessions/'+encodeURIComponent(recovered));
    if(snapshot.action_id!==pending.body.action_id||snapshot.source!=='camera')throw new Error('原回执与准备配置不匹配，请保留原标识并核对。');
    clearPending();sid=snapshot.session_id;root.dataset.sessionId=sid;
    if(snapshot.action_id!==root.dataset.actionId){location.assign('/realtime/'+sid);return}
    apply(snapshot);history.replaceState(null,'','/realtime/'+sid);showMessage('已恢复训练，请检查取景后继续。');
  }catch(caught){if(caught.status===404){clearPending();showMessage('原训练已不存在，请开始新的训练。');return}throw caught}
}
async function continueFlow(first=false){
  if(pendingCreate){await recoverCreate();return}
  const selected=target.value();
  if(sid&&current?.status==='ready'&&current.started_at_ms===null&&!target.matches(current.config,selected)){
    sid='';root.dataset.sessionId='';control=null;lastControl=null;current=null;restoredConfig=false;
  }
  const preview=await cameraControl.prepare();
  if(!sid){
    const request={commandId:crypto.randomUUID(),body:{action_id:root.dataset.actionId,capability_version:root.dataset.capabilityVersion,source:'camera',...(document.querySelector('#session-save-skeleton').checked?{save_skeleton:true}:{}),...selected}};
    try{sessionStorage.setItem(pendingKey,JSON.stringify(request));pendingCreate=request}catch{throw new Error('无法保存本页请求标识，尚未发送创建，请检查浏览器存储后重试。')}
    let created;
    try{created=await api('/api/v1/sessions',{method:'POST',commandId:request.commandId,body:request.body})}
    catch(caught){if(!caught.unknown)clearPending();throw caught.unknown?new Error('训练状态待确认，请点击重新读取。'):caught}
    sid=created.session_id;root.dataset.sessionId=sid;clearPending();apply(created);
  }
  if(!owns()&&!(await acquire()))return;
  await refresh();
  const command=current.status==='ready'?'start':'resume';
  cameraControl.hold();
  let result;
  try{result=await api(`/api/v1/sessions/${sid}/commands`,{method:'POST',commandId:crypto.randomUUID(),body:payload(command,{preview_id:preview.preview_id,preview_token:preview.preview_token})})}
  catch(caught){
    try{await refresh()}catch{await cameraControl.suspend();throw caught}
    if(caught.unknown&&owns()&&current.save_state==='clean'&&['countdown','running','waiting'].includes(current.status)&&!document.hidden)result=current;
    else{if(current.save_state==='clean'&&['ready','paused'].includes(current.status))cameraControl.resumePreview();else await cameraControl.suspend();throw caught}
  }
  apply(result);
  if(!['countdown','running','waiting'].includes(result.status)||result.save_state!=='clean')throw new Error('开始状态尚未确认，请核对原会话。');
  stopped=false;cameraControl.attach({session_id:sid,...credentials()});
  history.replaceState(null,'','/realtime/'+sid);document.title='实时分析 · 健身动作助手';
}
async function command(name,extra={}){
  const send=async()=>{const result=await api(`/api/v1/sessions/${sid}/commands`,{method:'POST',commandId:crypto.randomUUID(),body:payload(name,extra)});apply(result);return result};
  if(name!=='pause')return send();
  const original=credentials();
  cameraControl.hold('正在暂停，核对最后保存状态…');
  try{
    for(let attempt=0;attempt<3;attempt++){
      await refresh();
      if(terminal.has(current.status))return current;
      if(!owns()||current.control.controller_epoch!==original.controller_epoch||current.control.stream_epoch!==original.stream_epoch)
        throw new RequestError('控制关系已变化，请核对当前页面。',409,{},'CONTROL_CONFLICT');
      if(current.status==='paused')return current;
      try{return await send()}
      catch(caught){
        // 只重试明确未执行的版本冲突；每次重新取版本，未知回执／权限变化均退出。
        if(caught.code!=='VERSION_CONFLICT'||caught.status!==409||attempt===2)throw caught;
      }
    }
  }finally{await cameraControl.suspend()}
}
async function cleanup(body,key){
  const csrf=await cameraControl.csrfToken();const response=await fetch(`/api/v1/sessions/${sid}/commands`,{method:'POST',credentials:'same-origin',cache:'no-store',headers:{'Content-Type':'application/json','X-CSRFToken':csrf,'Idempotency-Key':key},body:JSON.stringify(body),signal:AbortSignal.timeout(15000)});
  const result=await response.json();if(!response.ok)throw new RequestError(result.error?.message||'收尾未确认',response.status,{},result.error?.code);
  return result.data;
}
function savedTerminal(data){return (terminal.has(data.status)&&data.save_state==='clean')||(data.stopped&&data.terminal_persisted&&data.save_state==='clean')}
async function endDialog(target=null){
  if(!current||!owns())return false;
  const original=credentials();
  let key=crypto.randomUUID(),body=null,sent=false;
  async function resolveOriginal(){
    try{return await refresh()}
    catch(caught){
      if(caught.code==='STORAGE_UNAVAILABLE'||caught.status>=500)return cleanup(body,key);
      throw new Error('训练结束状态待确认，请重试。');
    }
  }
  return confirmDialog({title:target?'结束并保存后离开':'结束本次分析',message:'只保留已经完成并确认保存的动作或时间。尚未完成的动作不计入；保存未确认时留在当前页处理。',confirmLabel:target?'结束并保存后离开':'结束并保存',onConfirm:async()=>{
    // 只在用户确认时停止本页输入；单纯打开／取消弹窗不改变训练。
    cameraControl.hold('正在结束，核对最后保存状态…');
    try{
      let result;
      if(sent)result=await resolveOriginal();
      else for(let attempt=0;attempt<3;attempt++){
        try{const latest=await refresh();if(savedTerminal(latest)){result=latest;break}}
        catch(caught){if(!caught.unknown)throw caught} // 存储故障仍允许原控制关系走收尾。
        if(!owns()||current.control.controller_epoch!==original.controller_epoch||current.control.stream_epoch!==original.stream_epoch)
          throw new RequestError('控制关系已变化，请核对当前页面。',409,{},'CONTROL_CONFLICT');
        body=payload('end',{confirm:true});sent=true;
        try{result=await api(`/api/v1/sessions/${sid}/commands`,{method:'POST',commandId:key,body});break}
        catch(caught){
          if(caught.unknown){result=await resolveOriginal();break}
          sent=false;key=crypto.randomUUID();body=null;
          if(caught.code!=='VERSION_CONFLICT'||caught.status!==409||attempt===2)throw caught;
        }
      }
      if(!savedTerminal(result)&&!result.stopped&&!result.analysis_frozen&&result.save_state!=='save_failed')
        throw new Error('已停止采集，请再次确认训练是否结束。');
      stopped=true;
      if(result.status)apply(result);else if(current){current={...current,save_state:result.save_state,state_version:result.state_version,last_saved_at_ms:result.last_saved_at_ms,analysis_frozen:true};render()}
      if(savedTerminal(result)){if(!target&&current.started_at_ms!==null)location.assign('/records/'+sid);return true}
      showMessage('分析已停止，保存尚未确认。请重试保存或明确放弃未保存部分。');return false;
    }finally{await cameraControl.suspend()}
  }});
}
start.onclick=()=>runBusy(()=>continueFlow(true));
claim.onclick=()=>runBusy(acquire);
resume.onclick=()=>runBusy(async()=>{
  if(current.status==='paused'&&current.resume_from==='resting'&&current.rest_remaining_ms>0){await command('resume');return}
  if(['running','waiting'].includes(current.status))await command('pause');await cameraControl.asPreview();await continueFlow();
});
extendRest.onclick=()=>runBusy(()=>command('extend_rest'));
pause.onclick=()=>runBusy(async()=>{await command('pause');await cameraControl.suspend()});
cancelCountdown.onclick=()=>runBusy(async()=>{const result=await command('cancel_countdown');await cameraControl.suspend();if(result.status==='ready'){history.replaceState(null,'','/prepare?action='+encodeURIComponent(root.dataset.actionId));await cameraControl.asPreview()}});
end.onclick=()=>runBusy(()=>endDialog());
document.querySelector('#live-retry-save').onclick=()=>runBusy(async()=>{const result=await api(`/api/v1/sessions/${sid}/commands`,{method:'POST',commandId:crypto.randomUUID(),body:{command:'retry_save',expected_version:current.state_version}});apply(result);if(result.save_state!=='clean')showMessage('原批次尚未保存，分析继续冻结。')});
async function discardDialog(navigate=false){
  const body=cleanupPayload('discard_unsaved',{confirm:true,last_committed_version:current.state_version}),key=crypto.randomUUID();
  const result=await confirmDialog({title:'放弃未保存部分',message:`已确认保存至 ${dateFormat.format(current.last_saved_at_ms)}。其后未写入的动作、时间和事件会丢失且无法恢复，旧摘要保留。`,confirmLabel:'确认放弃未保存部分',danger:true,onConfirm:()=>cleanup(body,key)});
  if(!result?.unsaved_discarded)return false;
  stopped=true;current={...current,unsaved_discarded:true};await cameraControl.suspend();
  if(navigate){const suffix=result.terminal_persisted===false?'pending':'confirmed';location.assign('/records?discarded='+suffix)}
  return true;
}
document.querySelector('#live-discard').onclick=()=>runBusy(()=>discardDialog(true));
cameraControl.setPreviewListener(render);cameraControl.setSnapshotListener(apply);
window.fitnessLeaveGuard=async target=>{
  voice.suspend();
  if(stopped&&current?.unsaved_discarded)return true;
  if(current?.save_state==='save_failed'&&lastControl){
    const proceed=await confirmDialog({title:'内容尚未保存',message:'请留在当前页重试保存。若继续离开，需要再次明确确认放弃尚未保存部分，已有摘要保留。',confirmLabel:'继续放弃确认'});
    return proceed?discardDialog():false;
  }
  if(!sid||!current||current.status==='ready'||terminal.has(current.status)||!owns()){await cameraControl.suspend();return true}
  return endDialog(target);
};
document.addEventListener('visibilitychange',()=>{
  if(!document.hidden){refresh().catch(error);return}
  if(!owns()||!['running','countdown','waiting','resting'].includes(current.status))return;
  const body=payload('pause'),key=crypto.randomUUID();
  cameraControl.csrfToken().then(csrf=>fetch(`/api/v1/sessions/${sid}/commands`,{method:'POST',credentials:'same-origin',keepalive:true,headers:{'Content-Type':'application/json','X-CSRFToken':csrf,'Idempotency-Key':key},body:JSON.stringify(body)})).then(async response=>{if(response.ok)apply((await response.json()).data)}).catch(()=>{/* 未确认暂停由控制租约到期中断，不伪造后台成功。 */});
});
async function poll(){if(disposed)return;try{if(!document.hidden)await refresh()}catch(caught){error(caught)}finally{if(!disposed)pollTimer=setTimeout(poll,2000)}}
async function heartbeat(){
  if(disposed)return;
  try{if(!document.hidden&&owns()&&!terminal.has(current.status))await api(`/api/v1/sessions/${sid}/heartbeat`,{method:'POST',body:credentials()})}
  catch(caught){
    if(caught.status===409){try{await refresh();if(!owns())await cameraControl.suspend()}catch(queryError){await cameraControl.suspend();error(queryError)}}
    else error(caught);
  }finally{if(!disposed)heartbeatTimer=setTimeout(heartbeat,2000)}
}
window.addEventListener('pagehide',()=>{disposed=true;clearTimeout(pollTimer);clearTimeout(heartbeatTimer);clearInterval(displayTimer);control=null;lastControl=null});
render();poll();heartbeat();displayTimer=setInterval(()=>{if(!document.hidden)renderRest()},250);
