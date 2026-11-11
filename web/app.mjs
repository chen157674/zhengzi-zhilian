import {ACTIONS,Analyzer} from './analyzer.mjs';
const $ = id => document.getElementById(id);
const video=$('video'), canvas=$('overlay'), ctx=canvas.getContext('2d');
let action='squat', source=null, stream=null, objectURL=null, worker=null, modelReady=false;
let status='idle', epoch=0, busy=false, lastFrame=-1, lastSubmit=0, analysis=new Analyzer(action);
let session=null, revision=0, duration=0, lastClock=null, baseTime=0, lastPersist=0;
let pending=null, savePromise=null, saveFailed=false, loadingSource=false, modelTimer=null;
const terminal = () => status==='idle' || status==='finished';
const timeText = value => { const n=Math.max(0,Math.floor(value||0)); return `${Math.floor(n/60).toString().padStart(2,'0')}:${(n%60).toString().padStart(2,'0')}`; };
const sourceTime = () => source==='camera' ? performance.now()/1000 : video.currentTime;
const sourceNames={camera:'摄像头',video:'本地视频',demo:'内置演示'};
const stateNames={running:'进行中／未正常结束',paused:'已暂停',ended:'已结束',interrupted:'训练中断'};
const repText=rep=>rep.assessed===false?'幅度无法评估（机位）':rep.issue??'未触发幅度提示';

function feedback(message,warn=false) { if($('feedbackText').textContent!==message)$('feedbackText').textContent=message; $('feedback').classList.toggle('warn',warn); }
function updateControls() {
  const locked=!terminal() || loadingSource || saveFailed || Boolean(savePromise);
  document.querySelectorAll('input[name=action]').forEach(x=>x.disabled=locked);
  for(const id of ['camera','fileButton','demo']) $(id).disabled=locked;
  $('start').disabled=locked || !source || !modelReady;
  $('start').textContent=status==='finished'?'再练一次':'开始训练';
  $('pause').disabled=!['running','paused'].includes(status);
  $('pause').textContent=status==='paused'?'继续训练':'暂停';
  $('stop').disabled=!['running','paused','countdown'].includes(status);
  $('release').disabled=!source || !terminal() || saveFailed || Boolean(savePromise);
  $('retrySave').hidden=!saveFailed;
}
function updateAction() {
  const meta=ACTIONS[action]; $('trainingTitle').textContent=meta.name; $('setupHint').textContent=meta.setup;
  $('scope').textContent=meta.scope; $('unit').textContent=meta.unit;
  $('metricLabel').textContent=action==='squat'?'已完成动作':'累计保持时长';
  renderMetrics();
}
function detailItem(left,right) {const li=document.createElement('li');for(const text of [left,right]) {const span=document.createElement('span');span.textContent=text;li.append(span);}return li;}
function renderMetrics() {
  const data=analysis.snapshot(); $('metric').textContent=action==='squat'?data.reps.length:Math.floor(data.hold);
  $('duration').textContent=timeText(duration); $('observed').textContent=timeText(data.observed);
  $('issueCount').textContent=data.reps.filter(x=>x.issue).length+data.intervals.length;
  const list=$('details'); list.replaceChildren();
  if(action==='squat') for(const rep of [...data.reps].reverse().slice(0,20)) list.append(detailItem(`第 ${rep.index} 次`,repText(rep)));
  else for(const interval of [...data.intervals].reverse().slice(0,20)) list.append(detailItem(`${timeText(interval.start)}–${timeText(interval.end)}`,interval.issue));
  if(!list.children.length) {const li=document.createElement('li');li.className='muted';li.textContent=action==='squat'?'完整完成后，这里会记录每一次。':'保持过程中出现的提示会记录在这里。';list.append(li);}
}
function draw(poses,width,height) {
  const rect=canvas.getBoundingClientRect(), dpr=Math.min(devicePixelRatio||1,2);
  canvas.width=Math.round(rect.width*dpr);canvas.height=Math.round(rect.height*dpr);ctx.scale(dpr,dpr);
  if(poses.length!==1) return;
  const scale=Math.min(rect.width/width,rect.height/height), x=(rect.width-width*scale)/2,y=(rect.height-height*scale)/2;
  const pts=poses[0], links=[[11,12],[11,13],[13,15],[12,14],[14,16],[11,23],[12,24],[23,24],[23,25],[25,27],[24,26],[26,28],[27,29],[29,31],[28,30],[30,32]];
  ctx.strokeStyle='#c6f3aa';ctx.lineWidth=2;ctx.fillStyle='#e5ffd5';
  for(const [a,b] of links) if(pts[a]?.visibility>.6 && pts[b]?.visibility>.6){ctx.beginPath();ctx.moveTo(x+pts[a].x*width*scale,y+pts[a].y*height*scale);ctx.lineTo(x+pts[b].x*width*scale,y+pts[b].y*height*scale);ctx.stroke();}
  for(const i of [11,12,13,14,15,16,23,24,25,26,27,28]) if(pts[i]?.visibility>.6){ctx.beginPath();ctx.arc(x+pts[i].x*width*scale,y+pts[i].y*height*scale,3,0,Math.PI*2);ctx.fill();}
}
function initModel() {
  if(worker && modelReady) return;
  worker?.terminate();clearTimeout(modelTimer);busy=false;modelReady=false;
  $('modelState').textContent='正在加载本地姿态模型…';$('retryModel').hidden=true;
  worker=new Worker('./pose-worker.js');
  const fail=message=>{clearTimeout(modelTimer);worker?.terminate();worker=null;busy=false;modelReady=false;
    $('modelState').textContent='姿态模型加载或运行失败';$('retryModel').hidden=false;
    if(status==='running') pause(); feedback(`模型不可用：${message}。可以点击重新加载。`,true);updateControls();};
  worker.onerror=e=>{e.preventDefault();fail(e.message||'工作线程发生错误');};
  worker.onmessage=({data})=>{
    if(data.type==='ready'){clearTimeout(modelTimer);modelReady=true;$('modelState').textContent='姿态模型已就绪 · 本机处理';updateControls();}
    if(data.type==='error') fail(data.message);
    if(data.type==='result') {busy=false;if(data.epoch!==epoch || status!=='running')return;
      const before=analysis.reps.length;
      const result=analysis.update(data.poses,data.time,data.width,data.height);
      draw(data.poses,data.width,data.height);$('phase').textContent=result.phase;
      feedback(result.message,!result.valid || Boolean(result.issue));renderMetrics();
      if(analysis.reps.length!==before) {const rep=analysis.reps.at(-1);feedback(`完成第 ${rep.index} 次。${repText(rep)}`,Boolean(rep.issue));persist();}
    }
  };
  modelTimer=setTimeout(()=>fail('加载超时，请检查模型资源后重试'),60000);
  worker.postMessage({type:'init'});updateControls();
}
function disposeSource() {
  epoch++;lastFrame=-1;stream?.getTracks().forEach(t=>t.stop());stream=null;
  video.pause();video.srcObject=null;video.removeAttribute('src');video.load();
  if(objectURL){URL.revokeObjectURL(objectURL);objectURL=null;}
  source=null;$('sourceLabel').textContent='尚未选择画面';$('emptyStage').hidden=false;
  ctx.clearRect(0,0,canvas.width,canvas.height);$('phase').textContent='准备中';updateControls();
}
function waitMetadata() {
  return new Promise((resolve,reject)=>{
    const clear=()=>{clearTimeout(timeout);video.removeEventListener('loadeddata',ok);video.removeEventListener('error',bad);};
    const ok=()=>{clear();resolve();};const bad=()=>{clear();reject(new Error('视频无法解码，请选择浏览器可播放的 MP4 或 WebM 文件'));};
    const timeout=setTimeout(()=>{clear();reject(new Error('读取画面超时'));},20000);
    video.addEventListener('loadeddata',ok,{once:true});video.addEventListener('error',bad,{once:true});
    if(video.readyState>=2)ok();
  });
}
async function selectSource(kind,file=null) {
  if(!terminal()||saveFailed||loadingSource||savePromise)return;
  loadingSource=true;disposeSource();status='idle';updateControls();
  analysis=new Analyzer(action);duration=0;session=null;renderMetrics();
  feedback(kind==='camera'?'正在打开摄像头，请留意浏览器的权限提示。':'正在读取视频，请稍候…');
  try {
    if(kind==='camera'){
      if(!navigator.mediaDevices?.getUserMedia)throw new Error('当前浏览器不支持摄像头，请在本机地址使用 Edge 或 Chrome');
      stream=await navigator.mediaDevices.getUserMedia({video:{width:{ideal:1280},height:{ideal:720}},audio:false});
      video.srcObject=stream;await video.play();await waitMetadata();
    } else {
      if(kind==='video'){objectURL=URL.createObjectURL(file);video.src=objectURL;}
      else video.src='./demo.mp4';
      video.load();await waitMetadata();
    }
    source=kind;$('sourceLabel').textContent=sourceNames[kind];$('emptyStage').hidden=true;
    feedback('画面已准备好。点击开始训练后倒计时。');initModel();
  } catch(error){disposeSource();const message=error.name==='NotAllowedError'?'摄像头权限被拒绝，请在浏览器地址栏允许摄像头，或选择本地视频。':error.name==='NotFoundError'?'没有找到摄像头，请连接设备或选择本地视频。':error.message;feedback(message,true);}
  finally{loadingSource=false;updateControls();}
}
async function api(url,options={}) {
  const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),8000);
  try{const response=await fetch(url,{...options,signal:controller.signal,headers:{'Content-Type':'application/json',...options.headers}});
    const data=await response.json();if(!response.ok)throw new Error(data.error||'请求失败');return data;
  } finally{clearTimeout(timer);}
}
function snapshot(state) {return {...structuredClone(analysis.snapshot()),duration,revision:++revision,state};}
async function persist(state=status==='paused'?'paused':status==='finished'?'ended':'running') {
  if(!session)return;
  pending=snapshot(state);
  if(savePromise)return savePromise;
  const sessionID=session;
  savePromise=(async()=>{
    while(pending){const data=pending;pending=null;$('saveState').textContent='保存中…';
      try{await api(`/api/sessions/${sessionID}`,{method:'PUT',body:JSON.stringify(data)});saveFailed=false;$('saveState').textContent='已保存到本机';}
      catch(error){saveFailed=true;$('saveState').textContent='保存失败，结果仍在当前页面';feedback(`保存失败：${error.message}。请保持页面打开并重试。`,true);break;}
    }
  })();
  try{await savePromise;}finally{savePromise=null;updateControls();}
}
async function countdown(resuming=false) {
  status='countdown';epoch++;const token=epoch;$('countdown').hidden=false;updateControls();
  for(let n=3;n>0;n--){if(token!==epoch)return;$('countdown').textContent=n;await new Promise(r=>setTimeout(r,1000));}
  if(token!==epoch)return;$('countdown').hidden=true;
  if(document.hidden){status='paused';feedback('页面已隐藏，准备好后点击继续训练。');updateControls();await persist('paused');return;}
  try{await video.play();}catch(error){status='paused';feedback('视频无法播放，请检查文件后重新开始。',true);updateControls();await persist('paused');return;}
  if(token!==epoch)return;
  if(!resuming)baseTime=sourceTime();
  analysis.breakTracking();lastFrame=-1;lastClock=sourceTime();lastPersist=performance.now();status='running';
  feedback('正在检查画面，请保持单人完整入镜。');$('phase').textContent='检查画面';updateControls();
}
async function start() {
  if(!terminal()||!source||!modelReady||saveFailed||savePromise)return;
  status='starting';updateControls();
  analysis=new Analyzer(action);duration=0;lastClock=null;revision=0;pending=null;session=null;renderMetrics();
  if(source!=='camera'){video.pause();video.currentTime=0;}
  try{session=(await api('/api/sessions',{method:'POST',body:JSON.stringify({action,source})})).id;await countdown();}
  catch(error){status='idle';feedback(`无法建立训练记录：${error.message}。请检查本机服务后重试。`,true);updateControls();}
}
function pause() {
  if(status!=='running')return;
  tickClock();status='paused';epoch++;analysis.breakTracking();lastClock=null;
  video.pause();$('phase').textContent='已暂停';feedback('已暂停，次数和保持时长不会继续增加。');updateControls();persist('paused');
}
async function stop() {
  if(!['running','paused','countdown'].includes(status))return;
  tickClock();epoch++;analysis.breakTracking();status='finishing';lastClock=null;video.pause();$('countdown').hidden=true;
  $('phase').textContent='本次已结束';feedback('本次训练已结束，以下是实际记录的结果。');renderMetrics();updateControls();
  if(source==='camera')disposeSource();
  await persist('ended');status='finished';updateControls();await refreshHistory();
}
function tickClock(){if(status!=='running')return;const t=sourceTime();if(lastClock!==null&&t>=lastClock&&t-lastClock<1)duration+=t-lastClock;lastClock=t;}
async function sampleLoop(now) {
  requestAnimationFrame(sampleLoop);
  if(status!=='running'||!modelReady||busy||video.readyState<2||now-lastSubmit<80||video.currentTime===lastFrame)return;
  lastSubmit=now;lastFrame=video.currentTime;busy=true;const token=epoch;
  const width=Math.min(video.videoWidth,960),height=Math.round(video.videoHeight*width/video.videoWidth);
  try{const frame=await createImageBitmap(video,{resizeWidth:width,resizeHeight:height});
    if(token!==epoch||status!=='running'||!worker){frame.close();busy=false;return;}
    worker.postMessage({type:'frame',frame,timestamp:performance.now(),epoch:token,time:Math.max(0,sourceTime()-baseTime),width,height},[frame]);
  }catch(error){busy=false;if(status==='running')pause();feedback(`画面读取失败：${error.message}`,true);}
}
async function refreshHistory() {
  const list=$('historyList');
  try{const rows=await api('/api/sessions');list.replaceChildren();
    if(!rows.length){const p=document.createElement('p');p.className='muted';p.textContent='还没有训练记录。完成第一次训练后，会自动保存在这里。';list.append(p);}
    for(const row of rows){const item=document.createElement('article');item.className='history-row';
      const title=document.createElement('div'), strong=document.createElement('strong'), date=document.createElement('small');
      strong.textContent=ACTIONS[row.action]?.name??row.action;date.textContent=new Date(row.created).toLocaleString('zh-CN',{hour12:false});title.append(strong,date);item.append(title);
      for(const value of [row.action==='squat'?`${row.reps.length} 次`:`${Math.floor(row.hold)} 秒`,sourceNames[row.source]??row.source,stateNames[row.state]??row.state]){const span=document.createElement('span');span.textContent=value;item.append(span);}
      const details=document.createElement('details'),summary=document.createElement('summary'),ul=document.createElement('ul');summary.textContent='查看明细';details.append(summary);
      for(const rep of row.reps){const li=document.createElement('li');li.textContent=`第 ${rep.index} 次 · ${timeText(rep.start)}–${timeText(rep.end)} · ${repText(rep)}`;ul.append(li);}
      for(const interval of row.intervals){const li=document.createElement('li');li.textContent=`${timeText(interval.start)}–${timeText(interval.end)} · ${interval.issue}`;ul.append(li);}
      if(!ul.children.length){const li=document.createElement('li');li.textContent='没有完整动作或已触发的问题记录；这不代表姿态全部正确。';ul.append(li);}
      details.append(ul);item.append(details);list.append(item);}
  }catch(error){list.replaceChildren();const p=document.createElement('p');p.className='error';p.textContent=`记录读取失败：${error.message}，可点击刷新重试。`;list.append(p);}
}
document.querySelectorAll('input[name=action]').forEach(input=>input.addEventListener('change',()=>{if(!terminal()||saveFailed)return;action=input.value;analysis=new Analyzer(action);duration=0;session=null;status='idle';epoch++;ctx.clearRect(0,0,canvas.width,canvas.height);updateAction();updateControls();feedback('动作已切换，请按机位说明准备画面。');}));
$('camera').onclick=()=>selectSource('camera');$('fileButton').onclick=()=>$('file').click();
$('file').onchange=()=>{const file=$('file').files[0];if(file)selectSource('video',file);$('file').value='';};
$('demo').onclick=()=>{const input=document.querySelector('input[value=squat]');input.checked=true;input.dispatchEvent(new Event('change'));selectSource('demo');};$('start').onclick=start;$('pause').onclick=()=>status==='paused'&&modelReady?countdown(true):pause();
$('stop').onclick=stop;$('release').onclick=()=>{disposeSource();feedback('画面已关闭。可以重新选择摄像头或视频。');};
$('retryModel').onclick=initModel;$('retrySave').onclick=async()=>{await persist();await refreshHistory();};$('refresh').onclick=refreshHistory;
video.addEventListener('ended',()=>{if(status==='running')stop();});
video.addEventListener('error',()=>{if(status==='running'){pause();feedback('视频解码中断，已暂停并保留结果。',true);}});
document.addEventListener('visibilitychange',()=>{if(document.hidden&&status==='running')pause();});
window.addEventListener('beforeunload',e=>{if(!terminal()||saveFailed){e.preventDefault();e.returnValue='';}});
window.addEventListener('pagehide',()=>{stream?.getTracks().forEach(t=>t.stop());if(session&&!terminal()){const data=snapshot('interrupted');fetch(`/api/sessions/${session}`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(data),keepalive:true}).catch(()=>{});}});
setInterval(()=>{tickClock();if(status==='running'){renderMetrics();if(performance.now()-lastPersist>2000){lastPersist=performance.now();persist();}}},250);
updateAction();updateControls();refreshHistory();requestAnimationFrame(sampleLoop);
