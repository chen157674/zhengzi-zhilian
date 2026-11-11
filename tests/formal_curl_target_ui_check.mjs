// 真实模型的新双侧浏览器切片；原业务边界和完整性能不重复运行。
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {BrowserFixture,root} from './formal_browser_support.mjs';

const evidence=path.join(root,'data/verification/step-19'),output=path.join(evidence,'curl-target-browser.json');
const extendSource=process.argv.includes('--extend-source'),previous=fs.existsSync(output)?JSON.parse(fs.readFileSync(output,'utf8')):null;
assert(!previous||extendSource&&previous.status==='failed','已有场景不自动重复；仅明确延长原失败输入');
const fixture=new BrowserFixture('ui-step19-curl-target');
const report={status:'running',scope:'One current Full browser target session, one set of two repetitions per anatomical side; public train fixture, not accuracy or physical camera acceptance',physical_camera:false,independent_truth:false};
if(previous){const {previous_runs=[],...prior}=previous;report.previous_runs=[...previous_runs,prior]}
fixture.env.FITNESS_CURL_FIXTURE_SECONDS=extendSource?'16':'12';
let page,context;const errors=[];
const save=()=>fs.writeFileSync(output,JSON.stringify(report,null,2)+'\n');
try{
  await fixture.startTestService('tests/formal_curl_target_service.py');
  context=await fixture.browser('Edge',{width:1440,height:1000});
  await fixture.apiLogin(context,'Curl_Admin','curl-target-fixture-password-123');
  report.source=await(await context.request.get(fixture.origin+'/__fixture/curl-source-info')).json();
  report.browser=await context.browser().version();
  report.source_hashes=Object.fromEntries(['fitness_app/static/camera.js','fitness_app/static/realtime.js','fitness_app/static/record_detail.js','fitness_app/action_counting.py','fitness_app/sessions.py','fitness_app/records.py', 'tests/formal_curl_target_service.py', 'tests/formal_curl_target_ui_check.mjs'].map(name=>[name,crypto.createHash('sha256').update(fs.readFileSync(path.join(root,name))).digest('hex')]));save();
  await context.route('**/*',route=>new URL(route.request().url()).origin===fixture.origin?route.continue():route.abort());
  await context.addInitScript(()=>{
    window.curlFixture={calls:0,video:null,tracks:[],states:[],observations:[],lastSeq:-1};
    const fetcher=window.fetch.bind(window);window.fetch=(input,options)=>{const pending=fetcher(input,options);if(typeof input==='string'&&input.includes('/sessions/')&&input.includes('/updates'))return pending.then(async response=>{if(response.ok){const d=(await response.clone().json()).data,f=d?.latest_frame;if(f&&f.frame_seq>curlFixture.lastSeq){curlFixture.lastSeq=f.frame_seq;curlFixture.observations.push({seq:f.frame_seq,captured_ms:f.captured_ms,code:f.framing.code,analysis:f.analysis,left_count:d.left_count,right_count:d.right_count})}}return response});return pending};
    Object.defineProperty(navigator.mediaDevices,'getUserMedia',{value:async constraints=>{
      if(constraints.audio!==false)throw Error('公开回放禁用麦克风');
      curlFixture.calls++;const video=document.createElement('video');video.muted=true;video.playsInline=true;video.preload='auto';video.src='/__fixture/curl-source';
      await new Promise((resolve,reject)=>{video.onloadeddata=resolve;video.onerror=()=>reject(Error('短回放不可解码'))});
      curlFixture.video=video;const canvas=document.createElement('canvas');canvas.width=video.videoWidth;canvas.height=video.videoHeight;
      const draw=()=>canvas.getContext('2d').drawImage(video,0,0,canvas.width,canvas.height);draw();
      const stream=canvas.captureStream(30),timer=setInterval(draw,33);
      for(const track of stream.getTracks()){const stop=track.stop.bind(track);track.stop=()=>{clearInterval(timer);video.pause();stop()};curlFixture.tracks.push(track)}
      return stream;
    }});
    Object.defineProperty(navigator.mediaDevices,'enumerateDevices',{value:async()=>[{kind:'videoinput',deviceId:'public-curl-target',label:'弯举公开素材回放 · 非真实摄像头'}]});
  });
  page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
  await page.goto(fixture.origin+'/prepare?action=standing_dumbbell_curl');
  await page.locator('input[name="training-mode"][value="target"]').check();
  assert.equal(await page.locator('[for="target-value"]').innerText(),'每侧次数');
  await page.locator('#target-sets').fill('1');await page.locator('#target-value').fill('2');await page.locator('#target-rest').fill('0');
  await page.locator('#camera-toggle').click();await page.waitForFunction(()=>!document.querySelector('#realtime-start').disabled);
  assert.equal((await(await context.request.get(fixture.origin+'/api/v1/records')).json()).data.total,0);
  await page.locator('#realtime-start').click();await page.waitForURL('**/realtime/*');
  const sid=new URL(page.url()).pathname.split('/').at(-1);report.session_id=sid;
  await page.locator('#live-state').filter({hasText:'分析中'}).waitFor();
  await page.evaluate(async()=>{
    const sample=()=>{
      const state=document.querySelector('#live-state').textContent,sides=document.querySelector('#live-sides').textContent,amount=document.querySelector('#live-amount').textContent,goal=document.querySelector('#live-goal').textContent;
      const key=JSON.stringify({state,sides,amount,goal});if(curlFixture.last!==key){curlFixture.last=key;curlFixture.states.push({state,sides,amount,goal,source_time:curlFixture.video.currentTime})}
    };
    curlFixture.observer=new MutationObserver(sample);curlFixture.observer.observe(document.querySelector('main'),{childList:true,characterData:true,subtree:true});sample();await curlFixture.video.play();
  });
  console.log('Real Full bilateral target replay started; physical camera disabled.');
  await page.locator('#live-state').filter({hasText:'已完成'}).waitFor({timeout:25000});
  report.ui=await page.evaluate(()=>{curlFixture.observer.disconnect();return {states:curlFixture.states,observations:curlFixture.observations,camera_calls:curlFixture.calls,tracks_released:curlFixture.tracks.every(t=>t.readyState==='ended'),amount:document.querySelector('#live-amount').textContent,sides:document.querySelector('#live-sides').textContent,goal:document.querySelector('#live-goal').textContent}});
  report.record=(await(await context.request.get(fixture.origin+'/api/v1/records/'+sid)).json()).data;save();
  const record=report.record;assert.equal(record.status,'completed');assert.equal(record.completed_sets,1);assert.equal(record.config.target.unit,'per_side_reps');assert(record.left_count>=2&&record.right_count>=2);assert.equal(Math.min(record.left_count,record.right_count),2);assert.equal(record.total_count,record.left_count+record.right_count);
  assert.equal(record.events.length,record.total_count);assert.deepEqual(record.events.map(e=>e.event_seq),Array.from({length:record.total_count},(_,i)=>i+1));assert.equal(record.events.filter(e=>e.side==='left').length,record.left_count);assert.equal(record.events.filter(e=>e.side==='right').length,record.right_count);assert(record.events.every(e=>e.algorithm_version==='cycles-four-actions-development-1'));
  assert(report.ui.states.some(s=>s.state==='分析中'&&/左 1 次 · 右 2 次|左 2 次 · 右 1 次/.test(s.sides)),'单侧先达2次时仍须继续等待另一侧');
  assert.equal(report.ui.amount,String(record.total_count));assert.equal(report.ui.sides,`左 ${record.left_count} 次 · 右 ${record.right_count} 次`);assert(report.ui.goal.includes('已完成 1 / 1 组'));assert.equal(report.ui.camera_calls,1);
  await page.waitForFunction(()=>curlFixture.tracks.every(t=>t.readyState==='ended'));report.ui.tracks_released=true;
  await page.evaluate(()=>window.scrollTo({top:0,behavior:'instant'}));await page.screenshot({path:path.join(evidence,'curl-target-completed.png'),fullPage:true});
  await page.locator('#live-result').click();await page.waitForURL('**/records/*');await page.locator('#detail-side').filter({hasText:`左侧 ${record.left_count} 次 · 右侧 ${record.right_count} 次`}).waitFor();
  report.detail={count:await page.locator('#detail-count').textContent(),sides:await page.locator('#detail-side').textContent()};
  await page.evaluate(()=>window.scrollTo({top:0,behavior:'instant'}));await page.screenshot({path:path.join(evidence,'curl-target-record.png'),fullPage:true});
  const again=(await(await context.request.get(fixture.origin+'/api/v1/records/'+sid)).json()).data;assert.equal(again.events.length,record.events.length);assert.equal(again.total_count,record.total_count);
  assert.deepEqual(errors,[]);report.status='passed';save();console.log(JSON.stringify({status:report.status,left:record.left_count,right:record.right_count,total:record.total_count,completed_sets:record.completed_sets}));
}catch(error){report.status='failed';report.error=error.message;report.page_errors=errors;save();if(page&&!page.isClosed()){report.ui_diagnostic=await page.evaluate(()=>({state:document.querySelector('#live-state')?.textContent,message:document.querySelector('#camera-message')?.textContent,error:document.querySelector('#live-error')?.textContent,states:window.curlFixture?.states,observations:window.curlFixture?.observations})).catch(()=>null);save()}throw error}
finally{await fixture.close();report.cleanup={owned_fixture_removed:!fs.existsSync(fixture.temporary),browser_and_service_closed:true};save()}
