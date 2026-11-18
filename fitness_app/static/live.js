import {coachingCue,cueDetails,paintCue} from './correction_cue.js';
import {api,confirmDialog,RequestError} from './app.js';
import {cameraConstraints,cameraAvailability,captureDimensions} from './camera_support.js';
import {LiveRetest} from './retest_ui.js';
const retest=new LiveRetest();
const $=id=>document.getElementById(id),video=$('live-source'),canvas=$('live-canvas'),ctx=canvas.getContext('2d'),capture=document.createElement('canvas'),cctx=capture.getContext('2d');
const pageId=crypto.randomUUID(),selection=$('live-selection'),toggle=$('live-toggle'),finish=$('live-finish'),error=$('live-error'),stage=$('live-stage');
let preferredFacing=matchMedia('(pointer:coarse)').matches?'environment':'user';
let trainingId=null;let stream=null,preview=null,lastPreview=null,busy=false,running=false,disposed=false,frameSeq=0,epoch=0,lastStamp=-1,frameImage=null,lastSkeleton=null,csrf=null,frameLoop=null,pollController=null,records=new Set(),saveFailed=false,cueCollapsed=false,lastDrawnAt=0;
let lastRetestCue=null,opening=false,interruption=null,releasing=null,lastVideoTime=-1,lastVideoAdvance=0;
let checkingRecovery=true,busyLabel='',busyControl=null,manualMode=false,lastManualAction='';
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
function fail(e){error.textContent=e.message;error.hidden=false;render();if(e.status===401){stopTracks();location.replace('/login?returnTo=%2Frealtime');}}
function modeLabel(){return manualMode?'手动指定':'自动识别';}
function status(text){$('live-status').textContent=text;}
async function token(){csrf=(await api('/api/v1/auth/csrf')).csrf_token;return csrf;}
function training(data){if(!data)return;const statistics=data.stability;$('live-stability-save-state').textContent=['partial','limited'].includes(statistics?.status)?'部分运动数据未保存，动作反馈仍可查看。':'';const sk=data.skeleton;$('live-skeleton-save-state').textContent=sk?.status==='limited'?'轨迹已达到上限，停止新增；训练摘要继续保存。':sk?.status==='save_failed'?'请重试保存骨架，训练摘要按原流程保存。':sk?.status==='deleted'?'本段骨架已删除，停止新增轨迹。':'';for(const id of data.saved_record_ids||[])records.add(id);saveFailed=data.save_state==='save_failed';$('live-save-banner').hidden=!saveFailed;if(saveFailed){stopTracks();$('live-cue').hidden=true;status(data.message||'保存未完成，分析已冻结。');}render();}
function render(){
 const pending=busy||Boolean(interruption)||checkingRecovery,ended=!$('live-summary').hidden;
 const phase=checkingRecovery?'正在准备':saveFailed?'待处理保存':busy?busyLabel||'正在处理':interruption?'正在暂停':ended?'训练已结束':running?'分析中':!error.hidden?'需要检查':lastPreview?'已暂停':'准备就绪';
 $('live-workspace').dataset.state=saveFailed?'save-failed':pending?'pending':ended?'ended':running?'running':!error.hidden?'error':lastPreview?'paused':'ready';
 $('live-phase').textContent=phase;
 toggle.dataset.state=pending&&(!busyControl||busyControl===toggle)?'loading':running?'pause':lastPreview?'play':'camera';
 toggle.disabled=pending||saveFailed;
 toggle.textContent=pending&&(!busyControl||busyControl===toggle)?(checkingRecovery?'正在准备…':busyLabel||'正在暂停…'):running?'暂停分析':records.size||lastPreview?'继续训练':'开启摄像头';
 finish.hidden=!lastPreview;finish.disabled=pending||saveFailed;
 for(const button of [toggle,finish,$('live-camera-flip'),$('live-save-retry'),$('live-save-discard')])button.setAttribute('aria-busy',String(busy&&busyControl===button));
 finish.textContent=busy&&busyControl===finish?'正在结束…':'结束训练';
 $('live-camera-flip').disabled=pending||saveFailed;
 $('live-save-retry').disabled=$('live-save-discard').disabled=pending;
 selection.disabled=pending||saveFailed;
 $('live-mode-auto').disabled=$('live-mode-manual').disabled=pending||saveFailed;
 $('live-mode-auto').setAttribute('aria-pressed',String(!manualMode));$('live-mode-manual').setAttribute('aria-pressed',String(manualMode));
 $('live-action-choice').textContent=manualMode?(selection.value?selection.selectedOptions[0].textContent:'请选择动作'):'自动判断动作';
 $('live-action-search-panel').inert=pending||saveFailed;
 if(pending||saveFailed)$('live-action-search-panel').open=false;
 $('live-device').disabled=running||pending||saveFailed;$('live-view').disabled=pending||saveFailed;
 $('live-save-skeleton').disabled=pending||running||Boolean(trainingId);
 $('live-fullscreen').disabled=!frameImage;
 $('live-empty').setAttribute('aria-busy',String(opening));
 $('live-empty-title').textContent=opening?'正在开启摄像头':!error.hidden?'正在准备摄像头':'准备开始训练';
 $('live-empty-text').textContent=opening?'请在浏览器提示中允许访问摄像头。':!error.hidden?'按上方提示检查后，点击「开启摄像头」重试。':'开启摄像头，开始这一组。';
}
function stopTracks(){$('live-retest-start').hidden=true;lastRetestCue=null;lastDrawnAt=0;renderGuide({message:'摄像头未采集，重新开启后检查取景。',steps:[],items:[]});delete $('live-workspace').dataset.aiSegment;delete document.querySelector('#live-workspace').dataset.previewId;running=false;epoch++;pollController?.abort();if(stream){for(const t of stream.getTracks())t.stop();stream=null;}video.srcObject=null;lastSkeleton=null;$('live-action').textContent=modeLabel()+' · '+(lastPreview?'已暂停':'等待开始');$('live-paused').hidden=!frameImage;$('live-cue').hidden=true;paint();render();}
function paint(){if(!frameImage)return;$('live-fullscreen').disabled=false;canvas.width=frameImage.width;canvas.height=frameImage.height;const w=canvas.width,h=canvas.height;ctx.save();if($('live-mirror').checked){ctx.translate(w,0);ctx.scale(-1,1);}ctx.drawImage(frameImage,0,0);const sk=lastSkeleton;if(running&&$('live-skeleton').checked&&sk?.available){const points=new Map(sk.joints.map(p=>[p.index,p]));ctx.lineWidth=Math.max(2,w/500);ctx.strokeStyle='#60d8c7';ctx.fillStyle='#60d8c7';for(const [a,b]of sk.connections){const x=points.get(a),y=points.get(b);if(!x||!y)continue;ctx.beginPath();ctx.moveTo(x.x*w,x.y*h);ctx.lineTo(y.x*w,y.y*h);ctx.stroke();}for(const p of sk.joints){ctx.beginPath();ctx.arc(p.x*w,p.y*h,4,0,Math.PI*2);ctx.fill();}for(const id of sk.highlight?.joint_indices||[]){const p=points.get(id);if(!p)continue;ctx.fillStyle='#f0b44c66';ctx.beginPath();ctx.arc(p.x*w,p.y*h,16,0,Math.PI*2);ctx.fill();ctx.fillStyle='#ecab33';ctx.beginPath();ctx.arc(p.x*w,p.y*h,6,0,Math.PI*2);ctx.fill();}}ctx.restore();}
function renderGuide(guide){if(!guide)return;$('live-framing-note').textContent=(guide.steps||[]).slice(0,2).join(' · ');}
function cResultAllowsCue(c){return coachingCue(c).state==='issue';}
function advice(result){const retestCue=result.coaching?.primary_cue,saved=result.automatic_analysis?.saved_record_id;lastRetestCue=cResultAllowsCue(result.coaching)&&retestCue&&saved?{cue:retestCue,record:saved}:null;$('live-retest-start').hidden=!lastRetestCue||Boolean(retest.option);retest.update(result);renderGuide(result.framing_guide);$('live-workspace').dataset.aiSegment=result.automatic_analysis?.segment_id||result.recognition?.action_id||'unconfirmed';const r=result.recognition||{};const name=selection.value?selection.selectedOptions[0].textContent:(r.action_id?document.querySelector('#live-selection option[value="'+CSS.escape(r.action_id)+'"]')?.textContent:null);$('live-action').textContent=modeLabel()+' · '+(name||(r.reason==='motion_unconfirmed'?'等待动作变化':'正在观察动作'));const c=result.coaching,cue=coachingCue(c);paintCue($('live-cue'),cue);$('live-cue').classList.toggle('needs-adjustment',cue.state==='issue');const info=cueDetails(cue.state==='issue'?c.primary_cue:null);const meta=info.body?'关注部位：'+info.body:'';if($('live-cue-meta').textContent!==meta)$('live-cue-meta').textContent=meta;$('live-cue-meta').hidden=!info.body||cueCollapsed;$('live-cue-collapse').hidden=!info.body;status('');}
async function release(){if(!preview)return;const old=preview;if(releasing?.preview===old)return releasing.promise;const pending={preview:old};releasing=pending;pending.promise=(async()=>{try{const res=await api('/api/v1/previews/'+old.preview_id,{method:'DELETE',commandId:crypto.randomUUID(),body:{preview_token:old.preview_token,stream_epoch:old.stream_epoch}});if(preview===old)preview=null;if(!disposed)training(res.training);}finally{if(releasing===pending)releasing=null;}})();return pending.promise;}
async function pause(){stopTracks();await frameLoop?.catch(()=>{});await release();status(saveFailed?'正在保存。':'已暂停，摄像头已关闭。');}
// 系统中断必须同步停流，不能排在开启/切镜头的busy门后等待。
function interrupt(message){stopTracks();status(message);if(!interruption){interruption=(async()=>{await frameLoop?.catch(()=>{});await release();})().catch(fail).finally(()=>{interruption=null;render();});}render();}
function current(own){return !disposed&&own===epoch&&!document.hidden;}
async function openCamera(){if(saveFailed)return;if(manualMode&&!selection.value){$('live-action-search-panel').open=true;throw new Error('请先选择要手动分析的动作，或切回自动识别。');}error.hidden=true;const own=++epoch;let acquired=null;opening=true;render();
 try{const retestConfig=await retest.payload();if(!current(own))return;if(preview)await release();if(!current(own))return;const unavailable=cameraAvailability();if(unavailable)throw new Error(unavailable);acquired=await navigator.mediaDevices.getUserMedia(cameraConstraints($('live-device').value,preferredFacing));if(!current(own)){acquired.getTracks().forEach(t=>t.stop());return;}stream=acquired;
 const track=stream.getVideoTracks()[0];for(const event of ['ended','mute'])track.addEventListener(event,()=>{if(current(own))interrupt(event==='ended'?'相机已断开或授权已撤销，请检查权限后点击继续训练。':'相机画面已中断，已暂停；请检查相机后点击继续训练。');},{once:true});
 video.srcObject=stream;await video.play();if(!current(own))return;await token();if(!current(own))return;const payload={page_id:pageId,coaching_view:$('live-view').value,recognition_mode:selection.value?'manual':'auto',save_training:true,...($('live-save-skeleton').checked?{save_skeleton:true}:{}),...retestConfig,...(trainingId?{training_id:trainingId}:{})};if(selection.value){payload.action_id=selection.value;payload.capability_version=selection.selectedOptions[0].dataset.capability;}preview=await api('/api/v1/previews',{method:'POST',commandId:crypto.randomUUID(),body:payload});lastPreview=preview;trainingId=preview.training_id||trainingId;
 if(!current(own)){try{await release();}catch(releaseError){if(!disposed)fail(releaseError);}return;}
 const preparationDeadline=performance.now()+180000;
 while(current(own)){
  const prepared=await api('/api/v1/previews/'+preview.preview_id);if(!current(own))return;
  if(prepared.status!=='active'||prepared.model_preparation?.state==='unavailable')throw new Error('正在准备动作模型，请稍后重试。');
  if(!prepared.model_preparation||prepared.model_preparation.state==='ready')break;
  if(performance.now()>preparationDeadline)throw new Error('动作模型准备超时，请稍后重试。');
  busyLabel='正在准备模型…';$('live-empty-title').textContent='正在准备动作分析';$('live-empty-text').textContent='首次启动需要稍等，准备好后再开始动作。';status('正在准备动作分析…');
  toggle.textContent=busyLabel;await sleep(300);
 }
 if(!current(own))return;
 const devices=await navigator.mediaDevices.enumerateDevices();if(!current(own))return;const device=$('live-device'),selected=track.getSettings().deviceId;device.replaceChildren(new Option('默认摄像头',''));for(const d of devices.filter(d=>d.kind==='videoinput'))device.add(new Option(d.label||'摄像头',d.deviceId,false,d.deviceId===selected));
 $('live-workspace').dataset.previewId=preview.preview_id;running=true;frameSeq=0;lastStamp=-1;lastVideoTime=-1;lastVideoAdvance=performance.now();$('live-empty').hidden=true;canvas.hidden=false;$('live-paused').hidden=true;$('live-fullscreen').disabled=false;render();frameLoop=loop(own);
 }catch(e){acquired?.getTracks().forEach(t=>t.stop());if(!current(own))return;stopTracks();if(preview)try{await release();}catch(releaseError){fail(releaseError);}const messages={NotAllowedError:'摄像头权限未允许，请在浏览器地址栏的权限设置中允许后重试。',NotFoundError:'没有找到摄像头，请连接设备后重试。',NotReadableError:'摄像头可能被其他程序占用，请关闭占用后重试。'};throw new Error(messages[e.name]||e.message);}finally{opening=false;}}
async function previewRequest(url,options={}){const controller=new AbortController();pollController=controller;const timer=setTimeout(()=>controller.abort(),10000);try{const response=await fetch(url,{credentials:'same-origin',cache:'no-store',...options,signal:controller.signal});const json=await response.json();if(!response.ok)throw new RequestError(json.error?.message||'分析请求失败',response.status,{},json.error?.code);return json.data;}finally{clearTimeout(timer);if(pollController===controller)pollController=null;}}
async function loop(own){try{while(running&&current(own)){
 const track=stream?.getVideoTracks()[0];if(!track||track.readyState!=='live'||track.muted||!track.enabled)throw new Error('相机画面已中断，请检查相机后继续。');
 if(video.currentTime===lastVideoTime){if(performance.now()-lastVideoAdvance>5000)throw new Error('相机没有提供新画面，已暂停；请重新开启摄像头。');await sleep(35);continue;}lastVideoTime=video.currentTime;lastVideoAdvance=performance.now();
 const capturedAt=performance.now(),dimensions=captureDimensions(video.videoWidth,video.videoHeight);capture.width=dimensions.width;capture.height=dimensions.height;cctx.drawImage(video,0,0,capture.width,capture.height);const bitmap=await createImageBitmap(capture);try{const blob=await new Promise(resolve=>capture.toBlob(resolve,'image/jpeg',.85));if(!blob)throw new Error('请重新获取摄像头画面。');if(!running||!current(own))break;const seq=++frameSeq,stamp=Math.max(lastStamp+1,Math.round(capturedAt));lastStamp=stamp;const body=new FormData();body.append('metadata',JSON.stringify({stream_epoch:preview.stream_epoch,frame_seq:seq,captured_ms:stamp,width:capture.width,height:capture.height}));body.append('frame',blob,'frame.jpg');await previewRequest('/api/v1/previews/'+preview.preview_id+'/frames',{method:'POST',headers:{'X-CSRFToken':csrf,'X-Preview-Token':preview.preview_token},body});
 const deadline=performance.now()+10000;while(running&&current(own)){const snap=await previewRequest('/api/v1/previews/'+preview.preview_id);if(!running||!current(own))break;training(snap.training);if(!running||!current(own))break;if(snap.status!=='active')throw new Error('分析输入已结束，请重新开启摄像头。');if(snap.result?.frame_seq===seq){const decoded=await createImageBitmap(bitmap);if(!running||!current(own)){decoded.close();break;}frameImage?.close();frameImage=decoded;canvas.dataset.frameSeq=String(seq);canvas.dataset.capturedMs=String(stamp);lastSkeleton=snap.result.skeleton;lastDrawnAt=performance.now();paint();advice(snap.result);break;}if(performance.now()>deadline)throw new Error('分析超时，请检查服务后继续。');await sleep(35);} }finally{bitmap.close();}await sleep(15);}}catch(e){if(running&&current(own)){stopTracks();fail(e);try{await release();}catch(releaseError){fail(releaseError);}}}}
async function run(task,label='正在处理…',control=toggle){if(busy||interruption||checkingRecovery)return;busy=true;busyLabel=label;busyControl=control;render();try{await task();}catch(e){fail(e);}finally{busy=false;busyLabel='';busyControl=null;render();}}
toggle.onclick=()=>run(()=>running?pause():openCamera(),running?'正在暂停…':'正在开启…');
$('live-retest-start').onclick=()=>run(async()=>{const selected=lastRetestCue;if(!selected)return;await pause();if(saveFailed)return;const value=await api('/api/v1/records/'+selected.record+'/retest-options');const option=value.options.find(v=>v.code===selected.cue.code&&v.side===(selected.cue.side||'none')&&v.rule_version===selected.cue.rule_version&&v.view===selected.cue.evidence?.view);if(!option)throw new Error('这项提示还没有可复测的完整保存来源，请在记录中重新选择。');location.assign('/realtime?'+new URLSearchParams({retest_record:option.origin.session_id,retest_evidence:option.origin.evidence_id}));});
$('live-camera-flip').onclick=()=>run(async()=>{const active=running;if(active)await pause();preferredFacing=preferredFacing==='user'?'environment':'user';$('live-device').value='';$('live-mirror').checked=preferredFacing==='user';$('live-camera-flip').textContent=preferredFacing==='user'?'切换后置相机':'切换前置相机';if(active&&!saveFailed)await openCamera();},'正在切换镜头…',$('live-camera-flip'));
selection.onchange=()=>{
 manualMode=Boolean(selection.value);if(selection.value)lastManualAction=selection.value;
 return run(async()=>{const wasRunning=running;if(wasRunning)await pause();$('live-action').textContent=modeLabel()+' · '+(selection.value?selection.selectedOptions[0].textContent:'等待开始');if(wasRunning&&!saveFailed)await openCamera();},'正在切换动作…');
};
$('live-mode-auto').onclick=()=>{if(selection.disabled||!manualMode)return;selection.value='';selection.dispatchEvent(new Event('change',{bubbles:true}));};
$('live-mode-manual').onclick=()=>{
 if(selection.disabled)return;
 if(manualMode){$('live-action-search-panel').open=true;return;}
 if(running){$('live-action-search-panel').open=true;return;}
 manualMode=true;
 if(lastManualAction){selection.value=lastManualAction;selection.dispatchEvent(new Event('change',{bubbles:true}));}
 else{$('live-action').textContent='手动指定 · 请选择动作';render();$('live-action-search-panel').open=true;}
};
const actionSearchPanel=$('live-action-search-panel'),actionSearch=$('live-action-search'),actionMatches=$('live-action-matches');
function findActions(){
 const query=actionSearch.value.trim();actionMatches.replaceChildren();
 for(const option of selection.options){
  if(!option.value||query&&!option.textContent.includes(query))continue;
  const button=document.createElement('button');button.type='button';button.className='action-search-result';button.textContent=option.textContent;button.setAttribute('aria-pressed',String(selection.value===option.value));
  button.onclick=()=>{if(selection.disabled)return;manualMode=true;selection.value=option.value;actionSearchPanel.open=false;selection.dispatchEvent(new Event('change',{bubbles:true}));};
  actionMatches.append(button);
 }
 if(!actionMatches.childElementCount){const empty=document.createElement('p');empty.className='field-help';empty.textContent='没有匹配的动作，试试其他名称。';actionMatches.append(empty);}
}
actionSearch.addEventListener('input',findActions);
actionSearchPanel.addEventListener('toggle',()=>{if(actionSearchPanel.open){findActions();actionSearch.focus();}});
actionSearchPanel.addEventListener('keydown',event=>{if(event.key==='Escape'){event.preventDefault();actionSearchPanel.open=false;actionSearchPanel.querySelector('summary').focus();}});
document.addEventListener('click',event=>{if(!actionSearchPanel.contains(event.target)&&!event.target.closest('#live-mode-manual'))actionSearchPanel.open=false;});
const deviceSettings=$('live-settings');
deviceSettings.addEventListener('keydown',event=>{if(event.key==='Escape'){event.preventDefault();deviceSettings.open=false;deviceSettings.querySelector('summary').focus();}});
document.addEventListener('click',event=>{if(!deviceSettings.contains(event.target))deviceSettings.open=false;});
$('live-view').onchange=()=>run(async()=>{const active=running;if(active)await pause();if(active&&!saveFailed)await openCamera();else renderGuide(await api('/api/v1/framing-guide'+(selection.value?'?action_id='+encodeURIComponent(selection.value):'')));});
$('live-mirror').onchange=$('live-skeleton').onchange=paint;
$('live-cue-collapse').onclick=()=>{cueCollapsed=!cueCollapsed;$('live-cue-meta').hidden=cueCollapsed||!$('live-cue-meta').textContent;$('live-cue-collapse').textContent=cueCollapsed?'展开详情':'收起详情';$('live-cue-collapse').setAttribute('aria-expanded',String(!cueCollapsed));};
async function end(){const yes=await confirmDialog({title:'结束本次训练？',message:'结束后查看本次已保存的动作片段；未完成周期不计入。',confirmLabel:'结束训练'});if(!yes)return false;await pause();if(saveFailed)return false;if(document.fullscreenElement===$('live-workspace'))await document.exitFullscreen();$('live-workspace').classList.remove('page-expanded');syncFullscreen();$('live-summary').hidden=false;stage.hidden=true;document.querySelector('.live-toolbar').hidden=true;$('live-summary-message').textContent=records.size?'已保存'+records.size+'个动作片段，可以分别回看反馈。':'本次没有形成可保存的完整动作或姿态观察。';const list=$('live-summary-records');list.replaceChildren();for(const id of records){const a=document.createElement('a');a.className='security-row';const intent=new URLSearchParams(location.search),carry=new URLSearchParams();for(const k of ['plan_workout_id','plan_item_id','retest_task'])if(intent.get(k))carry.set(k,intent.get(k));a.href='/records/'+id+(carry.size?'?'+carry:'');a.textContent=retest.option?'查看复测片段 →':'查看动作片段 →';list.append(a);}return true;}
finish.onclick=()=>run(end,'正在结束…',finish);$('live-again').onclick=()=>{$('live-save-skeleton').checked=false;records.clear();trainingId=null;lastPreview=null;stage.hidden=false;document.querySelector('.live-toolbar').hidden=false;$('live-summary').hidden=true;$('live-empty').hidden=false;canvas.hidden=true;frameImage?.close();frameImage=null;error.hidden=true;status('');$('live-action').textContent=modeLabel()+' · 等待开始';render();};
function syncFullscreen(){
 const expanded=document.fullscreenElement===$('live-workspace')||$('live-workspace').classList.contains('page-expanded');
 const label=expanded?'退出全屏':'全屏';$('live-fullscreen').querySelector('span').textContent=label;$('live-fullscreen').setAttribute('aria-label',label);$('live-fullscreen').title=label;$('live-fullscreen').setAttribute('aria-pressed',String(expanded));
 document.documentElement.classList.toggle('live-expanded',expanded);
}
$('live-fullscreen').onclick=async()=>{
 const workspace=$('live-workspace');
 if(workspace.classList.contains('page-expanded')){workspace.classList.remove('page-expanded');syncFullscreen();return;}
 try{if(document.fullscreenElement===workspace)await document.exitFullscreen();else await workspace.requestFullscreen();}
 catch{workspace.classList.add('page-expanded');status('已切换页内放大，按 Esc 或点击退出全屏返回。');}
 syncFullscreen();
};
document.addEventListener('fullscreenchange',syncFullscreen);
document.addEventListener('keydown',e=>{if(e.key==='Escape'){$('live-workspace').classList.remove('page-expanded');syncFullscreen();}});
async function recovery(discard){if(!lastPreview)return;if(discard&&!await confirmDialog({title:'仅保留已保存内容？',message:'继续后将放弃待保存的批次。',confirmLabel:'放弃未保存内容',danger:true}))return;const p=lastPreview;training(await api('/api/v1/previews/'+p.preview_id+'/save-recovery',{method:'POST',body:{preview_token:p.preview_token,stream_epoch:p.stream_epoch,discard,...(p.owner_recovery?{owner_recovery:true}:{})}}));if(!saveFailed){if(preview)await release();status('保存状态已确认，请点击继续训练重新取景。');}}
$('live-save-retry').onclick=()=>run(()=>recovery(false),'正在重试保存…',$('live-save-retry'));$('live-save-discard').onclick=()=>run(()=>recovery(true),'正在确认保存…',$('live-save-discard'));
window.fitnessLeaveGuard=async()=>{if(saveFailed){status('请先处理未保存内容。');return false;}if(running||preview)return end();return true;};
document.addEventListener('visibilitychange',()=>{if(document.hidden&&(running||stream||opening||preview))interrupt('已切到后台并关闭相机；返回后请点击继续训练，重新确认取景。');});
window.addEventListener('offline',()=>{if(running||stream||opening||preview)interrupt('网络已断开，已暂停并关闭相机；恢复连接后请点击继续训练。');});
window.addEventListener('beforeunload',event=>{if(running||preview||saveFailed){event.preventDefault();event.returnValue='';}});
window.addEventListener('pagehide',()=>{disposed=true;stopTracks();frameImage?.close();frameImage=null;if(preview&&csrf){fetch('/api/v1/previews/'+preview.preview_id,{method:'DELETE',keepalive:true,headers:{'Content-Type':'application/json','X-CSRFToken':csrf,'Idempotency-Key':crypto.randomUUID()},body:JSON.stringify({preview_token:preview.preview_token,stream_epoch:preview.stream_epoch})}).catch(()=>{});}});
window.addEventListener('fitness-auth-revoked',stopTracks);
const freshness=setInterval(()=>{if(running&&lastDrawnAt&&performance.now()-lastDrawnAt>1000){lastSkeleton=null;paint();$('live-cue').hidden=true;}},250);window.addEventListener('pagehide',()=>clearInterval(freshness));
const requestedAction=new URLSearchParams(location.search).get('action');if(requestedAction&&[...selection.options].some(x=>x.value===requestedAction)){selection.value=requestedAction;manualMode=true;lastManualAction=requestedAction;selection.dispatchEvent(new Event('change',{bubbles:true}));}
if(preferredFacing==='environment'){$('live-mirror').checked=false;$('live-camera-flip').textContent='切换前置相机';}
render();

api('/api/v1/automatic-training/pending').then(data=>{if(disposed||!data.pending.length)return;const pending=data.pending[0];trainingId=pending.run_id;lastPreview={preview_id:pending.preview_id,owner_recovery:true};training(pending);status('上一次训练仍有未保存的内容，请先恢复。');}).catch(fail).finally(()=>{checkingRecovery=false;render();try{const intent=JSON.parse(sessionStorage.getItem('fitness-camera-intent')||'null');sessionStorage.removeItem('fitness-camera-intent');if(intent&&intent.user===document.body.dataset.userId&&intent.action===requestedAction&&Date.now()-intent.created>=0&&Date.now()-intent.created<10000&&!lastPreview&&!saveFailed&&!disposed&&!document.hidden)run(openCamera,'正在开启摄像头…',toggle);}catch{status('准备就绪，请主动开启摄像头。');}});

// 紧邻视频的手机提示按实际内容预留高度，长建议不会覆盖下方设置。
const cueSizeObserver=new ResizeObserver(()=>{const cue=$('live-cue');$('live-stage').style.setProperty('--live-cue-space',Math.ceil(cue.getBoundingClientRect().height+12)+'px');});
cueSizeObserver.observe($('live-cue'));window.addEventListener('pagehide',()=>cueSizeObserver.disconnect(),{once:true});
