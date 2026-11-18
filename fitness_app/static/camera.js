import {api,RequestError} from './app.js';
import {privateError} from './record_common.js';

const root=document.querySelector('#camera-preparation'),video=document.querySelector('#camera-source'),canvas=document.querySelector('#camera-canvas');
const toggle=document.querySelector('#camera-toggle'),devices=document.querySelector('#camera-device'),message=document.querySelector('#camera-message');
const buffer=document.createElement('canvas'),pageId=crypto.randomUUID();
let stream=null,preview=null,generation=0,opening=false,closing=0,timer=null,controller=null,csrf='',sequence=0,began=0;
let sessionBinding=null,lastResult=null,snapshotListener=null,previewListener=null;
let processedStamps=[],resultTimer=null,resultController=null,lastDrawn=-1;
const pendingFrames=new Map();
function stopFlow(){generation++;clearTimeout(timer);clearTimeout(resultTimer);timer=resultTimer=null;controller?.abort();resultController?.abort();controller=resultController=null;pendingFrames.clear();lastDrawn=-1}
const connections=[[11,12],[11,13],[13,15],[12,14],[14,16],[11,23],[12,24],[23,24],[23,25],[25,27],[24,26],[26,28],[27,29],[29,31],[28,30],[30,32]];
const failureMessages={NotAllowedError:'摄像头授权被拒绝，请在浏览器站点权限中允许后重试。',NotFoundError:'没有找到可用摄像头。',NotReadableError:'请关闭其他应用中的摄像头后重试。',OverconstrainedError:'请重新选择摄像头。'};
function status(text,error=false){message.textContent=text;message.classList.toggle('error',error)}
function updateControls(){toggle.disabled=opening||closing>0;toggle.textContent=opening?'正在开启…':stream?'关闭摄像头':'开启摄像头';toggle.dataset.action=stream?'camera.close':'camera.open';devices.disabled=opening||closing>0||!stream;document.querySelector('#realtime-start').disabled=true}
async function releasePreview(value,keepalive=false){
  if(!value||!csrf)return;
  const response=await fetch(`/api/v1/previews/${value.preview_id}`,{method:'DELETE',headers:{'Content-Type':'application/json','X-CSRFToken':csrf,'Idempotency-Key':crypto.randomUUID()},credentials:'same-origin',cache:'no-store',keepalive,signal:keepalive?undefined:AbortSignal.timeout(2000),body:JSON.stringify({preview_token:value.preview_token,stream_epoch:value.stream_epoch})});
  if(!response.ok)throw new Error('待关闭预览');
  if(!keepalive){for(let attempt=0;attempt<20;attempt++){const snapshot=await api('/api/v1/previews/'+value.preview_id);if(!snapshot.queue.processing)return;await new Promise(resolve=>setTimeout(resolve,100))}throw new Error('预览引擎仍在释放，请稍后重新开启。')}
}
async function close({leaving=false,notice=true}={}){
  closing++;
  stopFlow();
  const old=preview;preview=null;sessionBinding=null;lastResult=null;processedStamps=[];
  if(stream)for(const track of stream.getTracks())track.stop();stream=null;video.srcObject=null;
  canvas.hidden=true;canvas.getContext('2d').clearRect(0,0,canvas.width,canvas.height);document.querySelector('#camera-empty').hidden=false;
  document.querySelector('#capture-settings').textContent=document.querySelector('#submission-settings').textContent='';
  document.querySelector('#preview-model-status').textContent='预览未开启。';updateControls();
  status('当前未采集画面；继续分析前需要重新检查取景。');
  try{await releasePreview(old,leaving);if(notice)status('摄像头已关闭；准备预览未生成训练记录。')}
  catch{if(notice)status('摄像头已关闭，正在释放预览。',true)}
  finally{closing--;updateControls()}
}
async function refreshDevices(){
  const list=await navigator.mediaDevices.enumerateDevices(),current=stream?.getVideoTracks()[0]?.getSettings().deviceId;
  devices.replaceChildren();for(const [index,item] of list.filter(item=>item.kind==='videoinput').entries()){const option=document.createElement('option');option.value=item.deviceId;option.textContent=item.label||`摄像头 ${index+1}`;devices.append(option)}
  if(current)devices.value=current;devices.disabled=!stream||opening;
}
async function draw(result,packet,epoch){
  const bitmap=await createImageBitmap(packet.blob);
  if(epoch!==generation){bitmap.close();return}
  lastResult=result;
  if(!sessionBinding)previewListener?.(result);
  const context=canvas.getContext('2d');if(canvas.width!==result.width||canvas.height!==result.height){canvas.width=result.width;canvas.height=result.height}
  canvas.dataset.frameSeq=String(result.frame_seq);canvas.dataset.capturedMs=String(result.captured_ms);canvas.dataset.framingUsable=result.framing.usable?'1':'0';
  context.drawImage(bitmap,0,0);bitmap.close();
  const skeleton=result.skeleton;
  if(document.querySelector('#legacy-skeleton')?.checked!==false&&skeleton?.available){
    const points=new Map(skeleton.joints.map(p=>[p.index,p]));context.lineWidth=Math.max(1.5,canvas.width/500);context.strokeStyle='#60d8c7';context.fillStyle='#60d8c7';
    for(const [start,end]of skeleton.connections){const a=points.get(start),b=points.get(end);if(!a||!b)continue;context.beginPath();context.moveTo(a.x*canvas.width,a.y*canvas.height);context.lineTo(b.x*canvas.width,b.y*canvas.height);context.stroke();}
    for(const point of points.values()){context.beginPath();context.arc(point.x*canvas.width,point.y*canvas.height,3,0,Math.PI*2);context.fill();}
    for(const id of skeleton.highlight?.joint_indices||[]){const p=points.get(id);if(!p)continue;context.fillStyle='#ecab3380';context.beginPath();context.arc(p.x*canvas.width,p.y*canvas.height,12,0,Math.PI*2);context.fill();}
  }
  document.querySelector('#camera-empty').hidden=true;canvas.hidden=false;
  status(result.framing.ready_for_tracking?'取景已稳定，请保持所需部位完整入镜。':result.framing.message);
  document.querySelector('#preview-model-status').textContent=root.dataset.countingAvailable==='1'?'姿态模型已加载。准备画面不计次数。':'姿态预览模型已加载。';
  processedStamps.push({stamp:result.captured_ms,usable:result.framing.usable});if(processedStamps.length>60)processedStamps.shift();
  const span=processedStamps.at(-1).stamp-processedStamps[0].stamp,fps=processedStamps.length>1&&span>0?(processedStamps.length-1)*1000/span:null;
  const usableFps=fps===null?null:processedStamps.slice(1).filter(frame=>frame.usable).length*1000/span;
  document.querySelector('#submission-settings').textContent=`实际提交 ${result.width}×${result.height} · 处理 ${fps===null?'计算中':fps.toFixed(1)+' fps'} · 可用 ${usableFps===null?'计算中':usableFps.toFixed(1)+' fps'} · 本帧处理 ${Math.round(result.processing_ms)} ms · 画面与同一处理帧对齐`;
}
async function capture(epoch){
  if(epoch!==generation||(!preview&&!sessionBinding)||!stream)return;
  if(pendingFrames.size>=2){timer=setTimeout(()=>capture(epoch),15);return}
  const requestController=new AbortController();controller=requestController;const signal=requestController.signal;const timeout=setTimeout(()=>requestController.abort(),15000);
  try{
    if(!video.videoWidth||!video.videoHeight)throw new Error('请重新开启摄像头。');
    const capturedAt=performance.now();
    const scale=Math.min(1,1280/video.videoWidth,720/video.videoHeight),width=Math.max(1,Math.floor(video.videoWidth*scale)),height=Math.max(1,Math.floor(video.videoHeight*scale));
    if(buffer.width!==width||buffer.height!==height){buffer.width=width;buffer.height=height}buffer.getContext('2d').drawImage(video,0,0,buffer.width,buffer.height);
    const blob=await new Promise(resolve=>buffer.toBlob(resolve,'image/jpeg',.8));if(epoch!==generation)return;if(!blob)throw new Error('请重试获取预览画面。');
    const binding=sessionBinding?{...sessionBinding}:null;
    const seq=sequence++,metadata={stream_epoch:binding?binding.stream_epoch:preview.stream_epoch,frame_seq:seq,captured_ms:Math.floor(capturedAt-began),width:buffer.width,height:buffer.height};
    if(binding)metadata.controller_epoch=binding.controller_epoch;
    const file=new File([blob],'frame.jpg',{type:'image/jpeg'}),form=new FormData();form.append('metadata',JSON.stringify(metadata));form.append('frame',file);
    // 最多两份不可变 JPEG；显示时按结果序号解码对应文件，不使用已被下一帧覆盖的采集画布。
    pendingFrames.set(seq,{blob:file,submittedAt:capturedAt});
    const endpoint=binding?`/api/v1/sessions/${binding.session_id}`:`/api/v1/previews/${preview.preview_id}`;
    const tokenHeader=binding?{'X-Control-Token':binding.control_token}:{'X-Preview-Token':preview.preview_token};
    const response=await fetch(endpoint+'/frames',{method:'POST',credentials:'same-origin',cache:'no-store',headers:{'X-CSRFToken':csrf,...tokenHeader},body:form,signal});
    const reply=await response.json();if(!response.ok)throw new RequestError(reply.error?.message||'帧提交失败',response.status,{},reply.error?.code);
    if(epoch!==generation)return;
    if(!resultController&&resultTimer===null)pollResults(epoch);
    // 上传请求仍完全串行且最多30次/秒；只重叠下一帧的采集／上传与上一帧推理。
    timer=setTimeout(()=>capture(epoch),Math.max(0,Math.ceil(1000/30-(performance.now()-capturedAt))));
  }catch(error){if(epoch!==generation)return;await close({notice:false});if(!privateError(error))status(error.message||'帧提交未完成，请重新开启。',true)}
  finally{clearTimeout(timeout);if(epoch===generation&&controller===requestController)controller=null}
}
async function pollResults(epoch){
  resultTimer=null;
  if(epoch!==generation||(!preview&&!sessionBinding)||!stream||!pendingFrames.size)return;
  const requestController=new AbortController();resultController=requestController;const signal=requestController.signal;const timeout=setTimeout(()=>requestController.abort(),15000);
  try{
    if(performance.now()-pendingFrames.values().next().value.submittedAt>15000)throw new Error('姿态处理超时，请重新开启预览。');
    const binding=sessionBinding?{...sessionBinding}:null;
    const endpoint=binding?`/api/v1/sessions/${binding.session_id}/updates?limit=1`:`/api/v1/previews/${preview.preview_id}`;
    const current=await fetch(endpoint,{credentials:'same-origin',cache:'no-store',signal}),value=await current.json();
    if(epoch!==generation)return;
    if(!current.ok)throw new RequestError(value.error?.message||'预览读取失败',current.status,{},value.error?.code);
    if(binding){snapshotListener?.(value.data);if(epoch!==generation)return}
    else if(value.data.status!=='active')throw new Error(value.data.status==='failed'?'请重新开启姿态预览。':'预览已释放，请重新开启摄像头。');
    const result=binding&&value.data.latest_frame?{...value.data.latest_frame,skeleton:value.data.skeleton}:value.data.result;
    if(result&&result.frame_seq>lastDrawn){
      const packet=pendingFrames.get(result.frame_seq);
      if(packet){await draw(result,packet,epoch);if(epoch!==generation)return;lastDrawn=result.frame_seq}
      // 被服务端更新结果越过的较早帧直接丢弃，绝不积压补发或错配画面。
      for(const seq of pendingFrames.keys())if(seq<=result.frame_seq)pendingFrames.delete(seq);
    }
    if(epoch===generation&&pendingFrames.size)resultTimer=setTimeout(()=>pollResults(epoch),15);
  }catch(error){if(epoch!==generation)return;await close({notice:false});if(!privateError(error))status(error.message||'预览处理未完成，请重新开启。',true)}
  finally{clearTimeout(timeout);if(epoch===generation&&resultController===requestController)resultController=null}
}
async function open(deviceId=''){
  if(opening||closing>0)return;
  await close({notice:false});if(document.hidden)return;opening=true;updateControls();const epoch=generation;
  try{
    if(!navigator.mediaDevices?.getUserMedia)throw new Error('请使用 Edge 或 Chrome 从本机地址打开摄像头。');
    status('正在申请摄像头权限…');
    const media=await navigator.mediaDevices.getUserMedia({audio:false,video:{width:{ideal:1280},height:{ideal:720},frameRate:{ideal:30,max:30},...(deviceId?{deviceId:{exact:deviceId}}:{})}});
    if(epoch!==generation){for(const track of media.getTracks())track.stop();return}
    stream=media;video.srcObject=media;await video.play();
    csrf=(await api('/api/v1/auth/csrf')).csrf_token;
    const created=await api('/api/v1/previews',{method:'POST',commandId:crypto.randomUUID(),body:{action_id:root.dataset.actionId,capability_version:root.dataset.capabilityVersion,page_id:pageId}});
    if(epoch!==generation){await releasePreview(created);return}
    preview=created;sequence=0;began=performance.now();
    const settings=stream.getVideoTracks()[0].getSettings();document.querySelector('#capture-settings').textContent=`实际采集 ${settings.width??'未知'}×${settings.height??'未知'} · ${settings.frameRate?settings.frameRate.toFixed(1):'未知'} fps`;
    stream.getVideoTracks()[0].addEventListener('ended',async()=>{if(epoch!==generation)return;await close({notice:false});status('摄像头已断开或权限被撤销，请重新选择设备。',true)},{once:true});
    await refreshDevices();status('正在加载姿态预览…');capture(epoch);
  }catch(error){if(epoch!==generation)return;await close({notice:false});if(!privateError(error))status(failureMessages[error.name]||error.message||'请检查摄像头权限后重试。',true)}
  finally{opening=false;updateControls()}
}
toggle.onclick=()=>stream?close():open();devices.onchange=()=>open(devices.value);
document.querySelector('#camera-mirror').onchange=event=>canvas.classList.toggle('mirrored',event.target.checked);
if(navigator.mediaDevices)navigator.mediaDevices.addEventListener('devicechange',()=>{if(stream)refreshDevices().catch(()=>status('请重新开启摄像头并刷新设备列表。',true))});
document.addEventListener('visibilitychange',()=>{if(document.hidden&&(stream||opening))close()});
window.fitnessLeaveGuard=async()=>{await close({notice:false});return true};
window.addEventListener('pagehide',()=>{close({leaving:true,notice:false})});

export const cameraControl={
  pageId,
  setSnapshotListener(listener){snapshotListener=listener},
  setPreviewListener(listener){previewListener=listener},
  isOpen(){return Boolean(stream||opening)},
  hold(message='正在移交预览，等待会话确认…'){stopFlow();status(message)},
  resumePreview(){if(preview&&stream){generation++;capture(generation)}},
  current(){return preview&&stream?{...preview,ready:Boolean(lastResult?.framing.ready_for_tracking)}:null},
  async prepare(){
    if(!stream)await open();
    for(let attempt=0;attempt<120;attempt++){
      const current=this.current();if(current?.ready)return current;
      if(!stream)throw new Error(message.textContent||'正在准备摄像头');
      await new Promise(resolve=>setTimeout(resolve,100));
    }
    throw new Error('请调整取景，让肩、髋、膝、踝持续入镜。');
  },
  attach(binding){
    if(!stream)throw new Error('当前页摄像头已关闭，请重新取景。');
    stopFlow();preview=null;sessionBinding={...binding};lastResult=null;processedStamps=[];sequence=0;began=performance.now();capture(generation);
  },
  async suspend(){await close({notice:false})},
  async asPreview(){const selected=devices.value;await close({notice:false});await open(selected);return this.prepare()},
  async csrfToken(){if(!csrf)csrf=(await api('/api/v1/auth/csrf')).csrf_token;return csrf},
};
