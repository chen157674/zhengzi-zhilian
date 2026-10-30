import {feedbackSummary} from './feedback_summary.js';
import {videoCue,paintCue} from './correction_cue.js';
import {api,confirmDialog,showMessage} from './app.js';
import {duration,quantity,el,privateError,issueNames} from './record_common.js';
import {sendVideo,localPlayer,demoAttribution} from './video_source.js';

const root=document.querySelector('#video-page'),choose=document.querySelector('#video-file'),action=document.querySelector('#video-action'),start=document.querySelector('#video-start'),errorBox=document.querySelector('#video-error'),player=document.querySelector('#video-player'),source=localPlayer(player);
const completed=new Set(['completed','cancelled','interrupted','failed']),labels={queued:'等待分析',running:'正在分析',cancelling:'正在停止',completed:'分析完成',cancelled:'已取消 · 部分结果',interrupted:'已中断 · 部分结果',failed:'分析失败 · 已处理部分'};
const markerKey='fitness-video-pending:'+document.body.dataset.userId;
let demo=null,availableDemo=null,demos=[],demoInputKey=null,demoSourceAttempted=false,file=null,input=null,job=null,jid=root.dataset.jobId,busy=false,uploading=false,disposed=false,timer=null,pending=null,matchMode=false,detail=null,aborter=null,playable=false;
function failure(error){document.querySelector('#video-correction').hidden=true;liveSkeleton=null;document.querySelector('#video-live-skeleton').hidden=true;if(privateError(error)){source.clear();return}errorBox.textContent=error.message||'操作未确认，请查询原任务状态';errorBox.hidden=false}
function savePending(){sessionStorage.setItem(markerKey,JSON.stringify(pending))}
function render(){
  const selected=action.selectedOptions[0],available=selected?.dataset.available==='true'||root.dataset.observer==='true';
  action.disabled=busy||Boolean(job)||Boolean(pending)||Boolean(demo);start.disabled=busy||uploading||Boolean(job)||(!pending&&(input?.status!=='ready'||!action.value||!available||!playable));start.textContent=pending?'继续核对原启动':demo?'开始分析':'开始分析';
  const isDemo=Boolean(demo)||job?.session.source==='demo';document.querySelector('#video-demo-note').hidden=true;document.querySelector('#video-demo-note').textContent=job?.stopped?'内置演示结果 · 不计入个人训练统计。':job?'内置演示任务 · 结果不计入个人训练统计。':'演示分析';document.querySelector('#video-demo-credit').hidden=!isDemo;document.querySelector('#video-title').textContent='视频分析';document.querySelector('#video-demo-entry').hidden=Boolean(file||input||demo||job||pending);document.querySelector('#video-choices').hidden=Boolean(file||input||demo||job||pending);document.querySelector('#video-use-demo').disabled=busy||uploading||!availableDemo||Boolean(job)||Boolean(pending);
  document.querySelector('#video-framing').textContent=selected?.dataset.framing||'请选择要分析的动作。';
  document.querySelector('#video-framing').hidden=isDemo;document.querySelector('#video-capability').hidden=isDemo&&available;
  document.querySelector('#video-capability').textContent=available?(selected.dataset.note||''):(action.value?'此动作目前没有可用分析能力。':'');
  document.querySelector('#video-file-panel').hidden=!file&&!input&&!demo;
  document.querySelector('#video-file-name').textContent=demo?'内置演示 · '+(demo.display_title||demo.title):file?.name||(input?'原视频已上传，刷新后回看需重新选择原片':'');
  document.querySelector('#video-input-state').textContent=uploading?'正在上传到本机…':input?({validating:'正在校验实际编码和首帧…',ready:'视频校验通过',rejected:input.encoding.message,cleaned:'临时副本已清理',consumed:'视频由分析任务持有'})[input.status]||input.status:'';
  for(const id of ['video-select','video-replace','video-remove'])document.querySelector('#'+id).disabled=busy||uploading||Boolean(job)||Boolean(pending);
  document.querySelector('#video-drop').hidden=Boolean(file)||Boolean(job)||Boolean(demo);document.querySelector('#video-player-wrap').hidden=!source.has();
  document.querySelector('#video-settings').hidden=Boolean(job)||!(file||input||demo||pending);document.querySelector('#video-progress-panel').hidden=!job;
  root.dataset.stage=job?(job.stopped?'result':'processing'):'select';document.querySelector('#video-back').hidden=!job;document.querySelector('#video-subtitle').hidden=Boolean(job);
  const picker=document.querySelector('#video-demo-picker');picker.hidden=!isDemo||demos.length<2||Boolean(job);document.querySelector('#video-demo-select').disabled=busy||uploading||Boolean(pending)||Boolean(job);
  if(!job)return;
  document.querySelector('#video-title').textContent=job.stopped?'分析结果':'正在分析视频';
  document.querySelector('#video-player-caption').textContent=job.stopped?'原视频回看':'正在处理的画面';
  document.querySelector('#video-live-cue').textContent='';
  updateSkeleton(job);
  updateCorrection();
  const ended=completed.has(job.status)&&job.stopped;
  document.querySelector('#video-state').textContent=labels[job.status]||job.status;
  const progress=document.querySelector('#video-progress');if(job.total_ms){progress.value=job.status==='completed'?100:Math.min(99,job.processed_ms/job.total_ms*100)}else progress.removeAttribute('value');
  document.querySelector('#video-progress-text').textContent=`已处理至 ${duration(job.processed_ms||0)}${job.total_ms?' / '+duration(job.total_ms):' · 总时长未知'}`;
  document.querySelector('#video-save-state').textContent=job.save_state!=='clean'?'停止与保存分别确认：仍有未保存内容。':!job.terminal_persisted&&ended?'处理已停止，终态尚未写入。':job.cleanup_pending?'摘要已保存，临时文件仍待清理。':ended?'已保存当前实际处理结果。' :'';
  document.querySelector('#video-cancel').hidden=ended;document.querySelector('#video-cancel').disabled=busy||job.status==='cancelling';
  document.querySelector('#video-save-error').hidden=job.save_state==='clean'&&!job.cleanup_pending&&(!ended||job.terminal_persisted);document.querySelector('#video-results').hidden=!ended;
  document.querySelector('#video-save-error h2').textContent=job.save_state!=='clean'?'部分内容尚未保存':'任务收尾尚未完成';
  document.querySelector('#video-save-retry').textContent=job.cleanup_pending?'重试保存与清理':'重试保存';
  document.querySelector('#video-save-discard').hidden=job.save_state==='clean';
  document.querySelector('#video-skeleton-reason').textContent=job.skeleton_reason;
  if(ended){
    document.querySelector('#video-amount').textContent=job.counting_available?quantity(job.session):'—';document.querySelector('#video-result-note').textContent=!job.counting_available?'本动作暂不显示次数；姿态反馈见下方。':job.session.total_count===0&&job.session.hold_ms===0?(job.session.unit==='seconds'?'暂无已确认的保持时间。':'暂无已完成动作。'):job.status==='completed'?'仅统计已确认完成量。':'这是已处理部分，不代表完整视频。';
    document.querySelector('#video-detail').href='/records/'+job.session_id;document.querySelector('#video-again').href=job.session.source==='demo'?'/video?demo=1':'/video';document.querySelector('#video-match').hidden=source.has();document.querySelector('#video-match').textContent=job.session.source==='demo'?'重新加载演示原片':'重新选择原视频';
    if(job.session.source==='demo')document.querySelector('#video-result-note').textContent='演示结果，不计入个人训练统计。'+document.querySelector('#video-result-note').textContent;
    const events=document.querySelector('#video-events');events.replaceChildren();
    const summary=feedbackSummary(detail),ranges=summary.issues;
    for(const range of ranges){const row=el('article',undefined,'feedback-row');const left=el('div');left.append(el('strong',range.message||issueNames[range.code]||'姿态反馈'),el('p',duration(range.start_ms)+' – '+duration(range.end_ms),'muted'));row.append(left,el('p',range.suggestion||(range.status==='unassessable'?'此时段证据不足，无法判断。':'')));const button=el('button','定位片段 →','text-link');button.type='button';button.disabled=!source.has();button.onclick=()=>{player.pause();player.currentTime=range.start_ms/1000;player.scrollIntoView({block:'center'})};row.append(button);events.append(row);}
    if(!ranges.length)events.append(el('p',summary.title,'analysis-verdict'));
  }
}
async function releaseInput(){if(input&&!['consumed','cleaned'].includes(input.status)){await api('/api/v1/inputs/'+input.input_id,{method:'DELETE',commandId:crypto.randomUUID(),body:{}})}input=null}
async function selectFile(candidate){
  if(!candidate)return;if(candidate.size>1024**3)throw new Error('单文件不能超过 1 GiB');if(!candidate.size)throw new Error('文件为空');
  if(matchMode||job){await sendVideo(`/api/v1/records/${job.session_id}/source-match`,candidate);file=candidate;source.set(file);render();return}
  await releaseInput();source.clear();demo=null;demoInputKey=null;demoSourceAttempted=false;playable=false;file=candidate;input=null;uploading=true;aborter=new AbortController();render();
  try{input=await sendVideo('/api/v1/inputs',file,crypto.randomUUID(),aborter.signal);source.set(file);scheduleInput()}
  finally{uploading=false;aborter=null;render()}
}
function showDemoChoice(){
  demoAttribution(document.querySelector('#video-demo-credit'),availableDemo);
  const poster=document.querySelector('#video-demo-poster');
  if(availableDemo?.poster_url){poster.src=availableDemo.poster_url;poster.alt=availableDemo.title;}
  document.querySelector('#video-demo-status').textContent=availableDemo?(availableDemo.display_title||availableDemo.title):'内置演示原片暂不可用，可继续选择自己的视频。';
}
async function loadDemos(){try{const data=await api('/api/v1/demos');demos=data.demos;const requested=new URLSearchParams(location.search).get('demo_id');availableDemo=(requested?demos.find(item=>item.demo_id===requested):demos[0])||null;const picker=document.querySelector('#video-demo-select');picker.replaceChildren(...demos.map(item=>new Option(item.display_title||item.title,item.demo_id,false,item===availableDemo)));showDemoChoice();}catch(error){availableDemo=null;if(privateError(error))return;document.querySelector('#video-demo-status').textContent='暂时无法核对演示资源，可继续选择自己的视频。'}render()}
document.querySelector('#video-demo-select').onchange=()=>run(async()=>{availableDemo=demos.find(item=>item.demo_id===document.querySelector('#video-demo-select').value)||null;demoInputKey=null;await selectDemo();});
async function selectDemo(){
  if(!availableDemo)throw new Error('内置演示原片暂不可用');
  await releaseInput();source.clear();playable=false;file=null;demo=availableDemo;action.value=demo.action_id;demoInputKey??=crypto.randomUUID();render();
  input=await api('/api/v1/demo-inputs',{method:'POST',commandId:demoInputKey,body:{demo_id:demo.demo_id}});demoInputKey=null;
  if(disposed)return;source.setRemote(demo.video_url);const url=new URL(location.href);url.searchParams.set('demo','1');url.searchParams.set('demo_id',demo.demo_id);history.replaceState(null,'',url.pathname+url.search);scheduleInput();render();
}
async function restoreDemoSource(){demoSourceAttempted=true;const data=await api('/api/v1/records/'+job.session_id+'/demo-source');if(disposed)return;demo={...data,action_id:job.session.action_id};demoAttribution(document.querySelector('#video-demo-credit'),data);source.setRemote(data.video_url);render()}
function scheduleInput(){clearTimeout(timer);if(input?.status!=='validating'||disposed)return;timer=setTimeout(async()=>{try{input=await api('/api/v1/inputs/'+input.input_id);render();scheduleInput()}catch(error){failure(error)}},500)}
async function refreshJob(){
  clearTimeout(timer);if(!jid||disposed)return;
  try{job=await api('/api/v1/jobs/'+jid);if(job.session.source==='demo'&&!source.has()&&!demoSourceAttempted){try{await restoreDemoSource()}catch(error){failure(error)}}if(job.stopped&&completed.has(job.status))detail=await api('/api/v1/records/'+job.session_id);render()}
  catch(error){failure(error)}
  if(!disposed&&(!job||!job.stopped||job.save_state!=='clean'||!job.terminal_persisted))timer=setTimeout(refreshJob,1000);
}
async function run(task){if(busy)return;busy=true;errorBox.hidden=true;render();try{await task()}catch(error){failure(error)}finally{busy=false;render()}}
async function startJob(){
  if(!pending){pending={config:{action_id:action.value,capability_version:action.selectedOptions[0].dataset.capability,source:demo?'demo':'video',mode:'free',target:null,input_id:input.input_id,...(demo?{demo_id:demo.demo_id}:{})},sessionKey:crypto.randomUUID(),jobKey:crypto.randomUUID()};try{savePending()}catch(error){pending=null;throw new Error('无法保存启动恢复标识，本次尚未发送创建请求。')}}
  try{
    const session=await api('/api/v1/sessions',{method:'POST',commandId:pending.sessionKey,body:pending.config});
    pending.sid=session.session_id;pending.version=pending.version??session.state_version;savePending();
    job=await api('/api/v1/jobs/video',{method:'POST',commandId:pending.jobKey,body:{session_id:pending.sid,expected_version:pending.version}});
  }catch(error){
    if(!error.unknown&&!['VISION_BUSY','RESOURCE_BUSY'].includes(error.code)){
      pending=null;sessionStorage.removeItem(markerKey);
      if(error.code==='STALE_CAPABILITY'){
        const fresh=await api('/api/v1/actions/'+encodeURIComponent(action.value)),option=action.selectedOptions[0];
        option.dataset.capability=fresh.capability_version;option.dataset.available=String(fresh.counting_available);option.dataset.framing=fresh.view;option.dataset.note=fresh.counting_note||'';
        error.message='动作设置已更新，请核对后再次点击开始分析。';
      }
    }
    throw error;
  }
  jid=job.job_id;pending=null;sessionStorage.removeItem(markerKey);history.replaceState(null,'','/video/jobs/'+jid);player.pause();await refreshJob();
}
async function cancelJob(){
  await refreshJob();if(job.stopped)return;
  job=await api('/api/v1/jobs/'+jid+'/cancel',{method:'POST',commandId:crypto.randomUUID(),body:{expected_version:job.state_version}});render();
  for(let attempt=0;attempt<30&&!job.stopped;attempt++){await new Promise(resolve=>setTimeout(resolve,100));job=await api('/api/v1/jobs/'+jid)}
  await refreshJob();if(!job.stopped)throw new Error('任务仍在停止，请继续查询原任务，不要重复启动。');
}
document.querySelector('#video-use-demo').onclick=()=>run(selectDemo);
start.onclick=()=>run(startJob);action.onchange=render;
player.onloadeddata=()=>{playable=true;render()};
player.onerror=()=>{playable=false;if(demo||job?.session.source==='demo'){demoSourceAttempted=true;source.clear();failure(new Error('演示原片暂时无法播放，请重新加载原片。'));render();return}render();if(source.has())failure(new Error('浏览器无法回看该视频，请选择兼容的 MP4 / H.264 8-bit 文件。'))};
for(const id of ['video-select','video-replace'])document.querySelector('#'+id).onclick=()=>{matchMode=false;choose.click()};
document.querySelector('#video-match').onclick=()=>{if(job?.session.source==='demo'){run(restoreDemoSource);return}matchMode=true;choose.click()};
choose.onchange=()=>{const selected=[...choose.files];choose.value='';if(!selected.length)return;if(selected.length!==1){failure(new Error('每次只能选择一个视频'));return}run(()=>selectFile(selected[0]))};
document.querySelector('#video-remove').onclick=()=>run(async()=>{await releaseInput();source.clear();file=null;demo=null;demoInputKey=null});
const drop=document.querySelector('#video-drop');drop.ondragover=event=>event.preventDefault();drop.ondrop=event=>{event.preventDefault();if(busy||uploading||job||pending)return;const files=[...event.dataTransfer.files];if(files.length!==1){failure(new Error('每次只能拖入一个视频'));return}run(()=>selectFile(files[0]))};
document.querySelector('#video-cancel').onclick=()=>run(async()=>{await confirmDialog({title:'取消视频分析',message:'停止后保留已经确认的部分结果，未处理片段不会算作正常。',confirmLabel:'取消并保留部分结果',onConfirm:async()=>{await cancelJob();return true}})});
document.querySelector('#video-save-retry').onclick=()=>run(async()=>{await api('/api/v1/sessions/'+job.session_id+'/commands',{method:'POST',commandId:crypto.randomUUID(),body:{command:'retry_save',expected_version:job.session.state_version}});await refreshJob()});
document.querySelector('#video-save-discard').onclick=()=>run(async()=>{await confirmDialog({title:'放弃未保存部分',message:'尚未写入的动作和时间将丢失，已有摘要保留。',confirmLabel:'确认仅保留已保存内容',danger:true,onConfirm:async()=>{await api('/api/v1/sessions/'+job.session_id+'/commands',{method:'POST',commandId:crypto.randomUUID(),body:{command:'discard_unsaved',expected_version:job.session.state_version,last_committed_version:job.session.state_version,confirm:true}});await refreshJob();return true}})});
window.fitnessLeaveGuard=async()=>{
  if(pending){showMessage('原启动结果尚待核对，请先点击“继续核对原启动”。');return false}
  if(job&&!job.stopped)return confirmDialog({title:'离开视频分析',message:'取消并保存已经处理的部分后再离开。',confirmLabel:'取消并保留部分结果后离开',onConfirm:async()=>{await cancelJob();if(job.save_state!=='clean'||!job.terminal_persisted)throw new Error('保存尚未确认，请留在本页处理');return true}});
  if(job?.save_state!=='clean'&&job)return false;
  if(uploading){aborter?.abort();return true}if(input&&!job&&!pending)await releaseInput();source.clear();return true;
};
window.addEventListener('pagehide',()=>{disposed=true;clearTimeout(timer);source.clear();aborter?.abort()});
render();
(async()=>{try{await loadDemos();if(jid){await refreshJob();return}const saved=sessionStorage.getItem(markerKey);if(saved){pending=JSON.parse(saved);action.value=pending.config.action_id;input=await api('/api/v1/inputs/'+pending.config.input_id);if(pending.config.source==='demo'){demo=demos.find(item=>item.demo_id===pending.config.demo_id)||null;if(demo){availableDemo=demo;showDemoChoice();source.setRemote(demo.video_url)}}render();return}const active=(await api('/api/v1/jobs')).jobs;if(active.length){const box=document.querySelector('#video-current');box.hidden=false;box.append(el('span','已有本账号的视频任务。'),Object.assign(el('a','返回当前任务','text-link'),{href:'/video/jobs/'+active[0].job_id}))}if(new URLSearchParams(location.search).get('demo')==='1')await run(selectDemo)}catch(error){failure(error)}})();

let liveSkeleton=null;
function updateSkeleton(job){const overlay=document.querySelector('#video-live-skeleton');liveSkeleton=!job.stopped&&job.skeleton?.available?job.skeleton:null;overlay.hidden=true;player.controls=Boolean(job.stopped);if(liveSkeleton&&source.has()){player.pause();player.currentTime=liveSkeleton.captured_ms/1000;}else overlay.getContext('2d').clearRect(0,0,overlay.width,overlay.height);}
function paintSkeleton(){const sk=liveSkeleton,overlay=document.querySelector('#video-live-skeleton');if(!sk||Math.abs(player.currentTime*1000-sk.captured_ms)>100)return;overlay.hidden=false;overlay.width=player.videoWidth;overlay.height=player.videoHeight;const c=overlay.getContext('2d'),w=overlay.width,h=overlay.height,points=new Map(sk.joints.map(p=>[p.index,p]));c.clearRect(0,0,w,h);c.strokeStyle='#60d8c7';c.fillStyle='#60d8c7';c.lineWidth=Math.max(1,w/700);for(const[a,b]of sk.connections){const p=points.get(a),q=points.get(b);if(!p||!q)continue;c.beginPath();c.moveTo(p.x*w,p.y*h);c.lineTo(q.x*w,q.y*h);c.stroke();}for(const p of sk.joints){c.beginPath();c.arc(p.x*w,p.y*h,Math.max(2,w/320),0,Math.PI*2);c.fill();}}

player.addEventListener('seeking',()=>{document.querySelector('#video-live-skeleton').hidden=true;});
player.addEventListener('seeked',paintSkeleton);
function updateCorrection(){if(!job){document.querySelector('#video-correction').hidden=true;return;}paintCue(document.querySelector('#video-correction'),videoCue(job,detail,player.currentTime*1000));}
player.addEventListener('timeupdate',updateCorrection);
player.addEventListener('seeked',updateCorrection);
