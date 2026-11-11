// 实际实时页面与合成Canvas流；验证生命周期/迟到响应，不能证明手机硬件或识别准确率。
import assert from 'node:assert/strict';
import path from 'node:path';
import {BrowserFixture,root} from './formal_browser_support.mjs';

const fixture=new BrowserFixture('round3-camera-lifecycle'),errors=[];
const only=process.env.FITNESS_CAMERA_LIFECYCLE_SCOPE||'all';
let state,serial=0;
const selected=name=>only==='all'||only.split(',').includes(name);
try{
 fixture.seed("from fitness_app.paths import configure_environment;configure_environment();import os;from fitness_app.database import Database;from fitness_app.auth import initialize_admin;db=Database(os.environ['FITNESS_DATABASE_PATH']);db.initialize();initialize_admin(db,'Camera_Lifecycle_Admin','Camera-lifecycle-fixture-2026');db.dispose()");
 fixture.start();const browser=await fixture.browser('Edge',{width:390,height:844});await fixture.apiLogin(browser,'Camera_Lifecycle_Admin','Camera-lifecycle-fixture-2026');
 const page=await browser.newPage();page.on('pageerror',e=>errors.push(String(e)));
 await page.addInitScript(()=>{
  window.cameraRequests=0;window.testHidden=false;window.testStreams=[];window.cameraOptions=[];window.holdDevices=false;window.holdDecode=false;
  Object.defineProperty(document,'hidden',{configurable:true,get:()=>window.testHidden});
  window.setHidden=value=>{window.testHidden=value;document.dispatchEvent(new Event('visibilitychange'));};
  navigator.mediaDevices.getUserMedia=async options=>{
   window.cameraRequests++;window.cameraOptions.push(options);
   const surface=document.createElement('canvas');surface.width=640;surface.height=480;
   const ctx=surface.getContext('2d');let x=0;const draw=()=>{ctx.fillStyle='#e6eeeb';ctx.fillRect(0,0,640,480);ctx.fillStyle='#344d47';ctx.fillRect(x++%550,180,70,70);};draw();
   const media=surface.captureStream(15),timer=setInterval(draw,60),track=media.getVideoTracks()[0],stop=track.stop.bind(track);
   track.stop=()=>{clearInterval(timer);stop();};window.testStreams.push(media);return media;
  };
  navigator.mediaDevices.enumerateDevices=async()=>{if(window.holdDevices){window.devicesWaiting=true;await new Promise(resolve=>window.releaseDevices=resolve);}return [];};
  const decode=window.createImageBitmap.bind(window);
  window.createImageBitmap=async(...args)=>{if(window.holdDecode&&args[0] instanceof ImageBitmap){window.decodeWaiting=true;await new Promise(resolve=>window.releaseDecode=resolve);}return decode(...args);};
  const encode=HTMLCanvasElement.prototype.toBlob;
  HTMLCanvasElement.prototype.toBlob=function(...args){if(window.holdEncoding){window.encodingWaiting=true;window.encodingWaitingAt=performance.now();window.releaseEncoding=()=>encode.apply(this,args);}else return encode.apply(this,args);};
 });
 await page.route('**/api/v1/previews',async route=>{
  state.created++;const n=++serial;
  state.preview={preview_id:`10000000-0000-4000-8000-${String(n).padStart(12,'0')}`,preview_token:'canvas-fixture',stream_epoch:`20000000-0000-4000-8000-${String(n).padStart(12,'0')}`,training_id:'30000000-0000-4000-8000-000000000001'};
  state.creations.push(route.request().postDataJSON());if(state.holdCreate){state.createWaiting=true;await new Promise(resolve=>state.releaseCreate=resolve);}return route.fulfill({status:201,json:{data:state.preview}});
 });
 await page.route('**/api/v1/previews/*/frames',route=>{
  state.submitted++;const raw=route.request().postDataBuffer().toString('latin1');state.packet=JSON.parse(raw.match(/name="metadata"\r\n\r\n([^\r]+)\r\n/)[1]);state.firstPacket??={...state.packet};
  return route.fulfill({status:202,json:{data:{accepted:true}}});
 });
 await page.route('**/api/v1/previews/*',async route=>{
  const current=state;
  if(route.request().method()==='DELETE'){current.released++;if(current.holdRelease){current.releaseWaiting=true;await new Promise(resolve=>current.releaseDelete=resolve);}return route.fulfill({json:{data:{released:true,training:{save_state:'clean',saved_record_ids:[]}}}});}
  current.polled++;const packet={...current.packet};
  if(current.holdPoll&&current.packet){current.pollWaiting=true;await new Promise(resolve=>current.releasePoll=resolve);}
  try{await route.fulfill({json:{data:{...current.preview,status:'active',model_preparation:{state:current.modelLoading?'loading':'ready'},training:{save_state:current.lateSaveFailure?'save_failed':'clean',saved_record_ids:[]},result:{...packet,
   framing:{usable:true,message:'工程合成画面'},skeleton:{available:true,joints:[{index:11,x:.4,y:.4},{index:13,x:.3,y:.5}],connections:[[11,13]],highlight:{joint_indices:[13]}},
   recognition:{action_id:'barbell_overhead_press'},automatic_analysis:{segment_id:'fixture-segment',saved_record_id:'40000000-0000-4000-8000-000000000001'},
   coaching:{assessment_scope:'fixture',primary_cue:{code:'fixture-elbow',side:'right',message:'工程提示：右肘位置',suggestion:'工程用例，不是人体纠错结论。'}},
   framing_guide:{message:'工程取景提示',steps:[],items:[]}}}}});}catch(error){if(!current.allowAbortedPoll)throw error;}
 });
 async function fresh(){state={created:0,released:0,submitted:0,polled:0,creations:[],packet:null};await page.goto(fixture.origin+'/realtime');await page.locator('#live-toggle').waitFor();await page.locator('#live-toggle:not([disabled])').waitFor();return state;}
 async function start(){await page.locator('#live-toggle').click();await page.locator('#live-cue').waitFor({state:'visible'});assert.equal(await page.locator('#live-selection').inputValue(),'');}
 async function stopped(){await page.waitForFunction(()=>window.testStreams.length&&window.testStreams.every(s=>s.getTracks().every(t=>t.readyState==='ended')));assert.equal(await page.locator('#live-cue').isVisible(),false);assert.equal(await page.locator('#live-retest-start').isVisible(),false);assert.equal(await page.locator('#live-workspace').getAttribute('data-ai-segment'),null);}

 if(selected('opening')){
  await fresh();await page.evaluate(()=>window.holdDevices=true);await page.locator('#live-toggle').click();await page.waitForFunction(()=>window.devicesWaiting);
  await page.evaluate(()=>window.setHidden(true));await stopped();
  await page.evaluate(()=>window.releaseDevices());await page.locator('#live-toggle:not([disabled])').waitFor();assert.equal(state.submitted,0);assert.equal(state.created,state.released);
  await page.evaluate(()=>window.setHidden(false));assert.equal(await page.evaluate(()=>window.cameraRequests),1);console.log('Background during device enumeration stops camera immediately and does not start frame submission: passed.');
 }
 if(selected('model-preparation')){
  await fresh();state.modelLoading=true;await page.locator('#live-toggle').click();
  await page.locator('#live-empty-title').filter({hasText:'正在准备动作分析'}).waitFor();
  assert.equal(state.submitted,0);
  await page.evaluate(()=>window.setHidden(true));await stopped();
  await page.locator('#live-toggle:not([disabled])').waitFor();
  assert.equal(state.released,1);state.modelLoading=false;
  await page.evaluate(()=>window.setHidden(false));await page.waitForTimeout(400);
  assert.equal(state.submitted,0);assert.equal(await page.evaluate(()=>window.cameraRequests),1);
  console.log('Background during model preparation stops camera, releases preview and ignores later readiness: passed.');
 }
 if(selected('decode')){
  await fresh();await page.evaluate(()=>window.holdDecode=true);await page.locator('#live-toggle').click();await page.waitForFunction(()=>window.decodeWaiting);
  await page.locator('#live-toggle').click();await stopped();await page.evaluate(()=>window.releaseDecode());await page.locator('#live-toggle:not([disabled])').waitFor();
  assert.equal(await page.locator('#live-cue').isVisible(),false);assert.equal(await page.locator('#live-workspace').getAttribute('data-ai-segment'),null);assert.equal(await page.locator('#live-canvas').getAttribute('data-frame-seq'),null);assert.equal(state.released,1);
  console.log('Decoded old frame after pause cannot restore skeleton/cue/AI segment: passed.');
 }
 for(const event of ['ended','mute','offline'])if(selected(event)){
  await fresh();await start();await page.evaluate(event=>{if(event==='offline')window.dispatchEvent(new Event(event));else window.testStreams.at(-1).getVideoTracks()[0].dispatchEvent(new Event(event));},event);await stopped();
  await page.waitForFunction(()=>document.getElementById('live-toggle').disabled===false);await page.waitForTimeout(100);assert.equal(state.released,1);const count=state.submitted;await page.waitForTimeout(180);assert.equal(state.submitted,count);
  assert.equal(await page.evaluate(()=>window.cameraRequests),1);assert.match(await page.locator('#live-action').textContent(),/暂停|等待/);assert.equal(await page.locator('#live-paused').isVisible(),true);
  if(event==='ended'){await page.screenshot({path:path.join(root,'第四轮优化/实现核对/实时相机中断-窄屏.png')});}
  console.log(`Camera ${event} clears current feedback, stops submission, releases once and requires explicit restart: passed.`);
 }
 if(selected('late-poll')){
  await fresh();state.holdPoll=true;state.allowAbortedPoll=true;state.lateSaveFailure=true;await page.locator('#live-toggle').click();
  while(!state.pollWaiting)await page.waitForTimeout(25);
  await page.locator('#live-toggle').click();await stopped();state.releasePoll();await page.locator('#live-toggle:not([disabled])').waitFor();
  assert.equal(await page.locator('#live-save-banner').isVisible(),false);assert.equal(state.released,1);console.log('Late old poll cannot set current save failure or repaint current state: passed.');
 }
 if(selected('switch')){
  await fresh();await start();const oldEpoch=state.packet.stream_epoch;await page.locator('#live-camera-flip').click();
  await page.waitForFunction(()=>window.cameraRequests===2);await page.locator('#live-cue').waitFor({state:'visible'});
  assert.equal(state.released,1);assert.equal(state.created,2);assert.notEqual(state.packet.stream_epoch,oldEpoch);assert.equal(await page.evaluate(()=>window.testStreams[0].getTracks().every(t=>t.readyState==='ended')),true);
  assert.equal(state.creations[1].recognition_mode,'auto');assert.equal(state.creations[1].training_id,state.preview.training_id);assert.equal(await page.evaluate(()=>window.cameraOptions[1].audio),false);
  await page.locator('#live-toggle').click();await page.locator('#live-toggle:not([disabled])').waitFor();await stopped();console.log('Switching live camera releases old stream, uses new epoch, retains training run and auto mode: passed.');
 }
 if(selected('create-late')){
  await fresh();state.holdCreate=true;await page.locator('#live-toggle').click();while(!state.createWaiting)await page.waitForTimeout(25);
  await page.evaluate(()=>window.setHidden(true));await stopped();state.releaseCreate();await page.locator('#live-toggle:not([disabled])').waitFor();
  assert.equal(state.created,1);assert.equal(state.released,1);assert.equal(state.submitted,0);assert.equal(await page.locator('#live-workspace').getAttribute('data-preview-id'),null);
  await page.evaluate(()=>window.setHidden(false));assert.equal(await page.evaluate(()=>window.cameraRequests),1);console.log('Create response arriving after background is released without starting analysis or publishing stale preview: passed.');
 }
 if(selected('release-race')){
  await fresh();await start();state.holdRelease=true;await page.locator('#live-toggle').click();while(!state.releaseWaiting)await page.waitForTimeout(25);
  await page.evaluate(()=>window.setHidden(true));await stopped();state.releaseDelete();await page.locator('#live-toggle:not([disabled])').waitFor();assert.equal(state.released,1);
  await page.evaluate(()=>window.setHidden(false));assert.equal(await page.evaluate(()=>window.cameraRequests),1);console.log('Pause and background share one in-flight preview release, without duplicate finalization: passed.');
 }
 if(selected('frozen-video')){
  await fresh();await start();await page.locator('#live-source').evaluate(video=>Object.defineProperty(video,'currentTime',{configurable:true,get:()=>0}));
  await page.locator('#live-error').filter({hasText:'相机没有提供新画面'}).waitFor();await stopped();await page.locator('#live-toggle:not([disabled])').waitFor();assert.equal(state.released,1);
  console.log('Live track with frozen video time is not repeatedly submitted as new observations; five-second watchdog pauses: passed.');
 }
 if(selected('resume')){
  await fresh();await start();await page.evaluate(()=>window.setHidden(true));await stopped();await page.locator('#live-toggle:not([disabled])').waitFor();
  await page.evaluate(()=>window.setHidden(false));await page.waitForTimeout(100);assert.equal(await page.evaluate(()=>window.cameraRequests),1);
  const oldEpoch=state.packet.stream_epoch;await page.locator('#live-toggle').click();await page.locator('#live-cue').waitFor({state:'visible'});
  assert.equal(state.created,2);assert.equal(await page.evaluate(()=>window.cameraRequests),2);assert.notEqual(state.packet.stream_epoch,oldEpoch);assert.equal(state.creations[1].training_id,state.preview.training_id);
  await page.locator('#live-toggle').click();await page.locator('#live-toggle:not([disabled])').waitFor();console.log('Returning from background never auto-opens camera; explicit resume starts a new stream in the same training run: passed.');
 }
 if(selected('capture-time')){
  await fresh();await page.evaluate(()=>window.holdEncoding=true);await page.locator('#live-toggle').click();await page.waitForFunction(()=>window.encodingWaiting);await page.waitForTimeout(200);
  const encodingAt=await page.evaluate(()=>{window.holdEncoding=false;window.releaseEncoding();return window.encodingWaitingAt;});await page.locator('#live-cue').waitFor({state:'visible'});
  assert(state.firstPacket.captured_ms<=encodingAt+5,'source timestamp must precede encoding wait');await page.locator('#live-toggle').click();await page.locator('#live-toggle:not([disabled])').waitFor();
  console.log('Source timestamp refers to the captured image, not completion after delayed JPEG encoding: passed.');
 }
 if(selected('encoding-cancel')){
  await fresh();await page.evaluate(()=>window.holdEncoding=true);await page.locator('#live-toggle').click();await page.waitForFunction(()=>window.encodingWaiting);
  await page.locator('#live-toggle').click();await stopped();await page.evaluate(()=>window.releaseEncoding());await page.locator('#live-toggle:not([disabled])').waitFor();assert.equal(state.submitted,0);assert.equal(state.released,1);
  console.log('Pause during JPEG encoding never submits the late encoded frame: passed.');
 }
 assert.deepEqual(errors,[]);
}finally{await fixture.close();}
