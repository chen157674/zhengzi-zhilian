// 08 新会话页面联动；真实模型 + MIT 演示一帧回放，不是运动效果／现场相机验收。
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {BrowserFixture,root} from './formal_browser_support.mjs';

const fixture=new BrowserFixture('ui-step08'),compatibility=process.argv.includes('--compat-only');
const scenario=process.argv.find(arg=>arg.startsWith('--case='))?.slice(7)||'all';
const include=name=>scenario==='all'||scenario.split(',').includes(name);
const evidence=path.join(root,'data/verification/step-08');fs.mkdirSync(evidence,{recursive:true});
const prepared=JSON.parse(fixture.seed(`
import json,base64,av
from io import BytesIO
from PIL import Image
from fitness_app.paths import ROOT
with av.open(str(ROOT/'web/demo.mp4')) as container:
    frame=next(container.decode(video=0));image=Image.fromarray(frame.to_ndarray(format='rgb24'));image.thumbnail((1280,720));out=BytesIO();image.save(out,format='JPEG',quality=85)
print(json.dumps({'jpeg':base64.b64encode(out.getvalue()).decode(),'width':image.width,'height':image.height}))
`));
let context,page;const ids=new Set(),errors=[],conflicts=[];let creates=0,createRequests=0,ends=0,lastControlToken='';
function fault(blocked){fs.writeFileSync(path.join(fixture.temporary,'fault.json'),JSON.stringify({blocked}))}
async function snapshot(id){return (await(await context.request.get(fixture.origin+'/api/v1/sessions/'+id)).json()).data}
async function post(path,body){const csrf=(await(await context.request.get(fixture.origin+'/api/v1/auth/csrf')).json()).data.csrf_token;return context.request.post(fixture.origin+path,{data:body,headers:{'X-CSRFToken':csrf,'Idempotency-Key':crypto.randomUUID()}})}
try{
  await fixture.startTestService('tests/formal_sessions_service.py');
  context=await fixture.browser(compatibility?'Chrome':'Edge',compatibility?{width:1280,height:900}:{width:1440,height:1000});await fixture.apiLogin(context,'Flow_User','flow-fixture-password-123');
  await context.route('**/__fixture/flow.jpg',route=>route.fulfill({contentType:'image/jpeg',body:Buffer.from(prepared.jpeg,'base64')}));
  await context.addInitScript(({width,height})=>{
    window.fixtureCamera={calls:0,stops:0,streams:[]};
    Object.defineProperty(navigator.mediaDevices,'getUserMedia',{value:async()=>{
      const state=fixtureCamera;state.calls++;const image=new Image();image.src='/__fixture/flow.jpg';await image.decode();const canvas=document.createElement('canvas');canvas.width=width;canvas.height=height;
      const draw=()=>canvas.getContext('2d').drawImage(image,0,0,width,height);draw();const stream=canvas.captureStream(30),timer=setInterval(draw,33),track=stream.getVideoTracks()[0],stop=track.stop.bind(track);
      Object.defineProperty(track,'getSettings',{value:()=>({width,height,frameRate:30,deviceId:'flow-replay'})});
      Object.defineProperty(track,'stop',{value:()=>{if(track.readyState!=='ended')state.stops++;clearInterval(timer);stop()}});state.streams.push(stream);return stream;
    }});
    Object.defineProperty(navigator.mediaDevices,'enumerateDevices',{value:async()=>[{kind:'videoinput',deviceId:'flow-replay',label:'MIT 演示回放（非现场摄像头）'}]});
  },{width:prepared.width,height:prepared.height});
  page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));
  page.on('response',async response=>{if(response.status()===409){const request=response.request(),body=request.headers()['content-type']?.includes('application/json')?request.postDataJSON():null;conflicts.push({path:new URL(response.url()).pathname,command:body?.command,controller_epoch:body?.controller_epoch,stream_epoch:body?.stream_epoch})}});
  page.on('response',async response=>{if(response.request().method()!=='POST')return;const url=new URL(response.url());if(url.pathname==='/api/v1/sessions'&&response.status()===201){const data=(await response.json()).data;ids.add(data.session_id);creates++}if(url.pathname.endsWith('/control')&&response.status()===200){const data=(await response.json()).data;if(data.control_token)lastControlToken=data.control_token}});
  page.on('request',request=>{if(request.method()==='POST'&&new URL(request.url()).pathname==='/api/v1/sessions')createRequests++;if(request.method()==='POST'&&/\/sessions\/[^/]+\/commands$/.test(new URL(request.url()).pathname)){const body=request.postDataJSON();if(body.command==='end')ends++}});
  await page.goto(fixture.origin+'/prepare?action=bodyweight_squat');assert.equal(await page.evaluate(()=>fixtureCamera.calls),0);
  await page.locator('#camera-toggle').click();await page.waitForFunction(()=>!document.querySelector('#realtime-start').disabled);
  if(scenario==='marker_denied'){
    await page.evaluate(()=>{const original=Storage.prototype.setItem;Storage.prototype.setItem=function(key,value){if(this===sessionStorage&&key.startsWith('fitness-create-'))throw new DOMException('fixture storage denial','QuotaExceededError');return original.call(this,key,value)}});
    await page.locator('#realtime-start').click();await page.locator('#live-error').filter({hasText:'尚未发送创建'}).waitFor();assert.equal(createRequests,0);assert.equal(creates,0);assert.deepEqual(errors,[]);
    const stored=JSON.parse(fixture.seed("import os,json\nfrom fitness_app.database import Database\nfrom sqlalchemy import select,func\nfrom fitness_app.db_schema import analysis_sessions\ndb=Database(os.environ['FITNESS_DATABASE_PATH'])\nwith db.transaction(False) as c: print(json.dumps({'sessions':c.scalar(select(func.count()).select_from(analysis_sessions))}))\ndb.dispose()"));assert.equal(stored.sessions,0);
    console.log(JSON.stringify({scenario,createRequests,persistentSessions:0,checks:'passed'}));
  }else if(['create_unknown','create_not_sent'].includes(scenario)){
    let original=null;const requestKeys=[];const requestBodies=[];
    await page.route('**/api/v1/sessions',async route=>{if(route.request().method()!=='POST')return route.continue();requestKeys.push(route.request().headers()['idempotency-key']);requestBodies.push(route.request().postDataJSON());if(scenario==='create_not_sent'&&requestKeys.length===1)return route.abort('failed');const response=await route.fetch();assert.equal(response.status(),201);original=(await response.json()).data.session_id;ids.add(original);if(scenario==='create_unknown')await route.abort('failed');else await route.fulfill({response})});
    await page.locator('#realtime-start').click();await page.locator('#realtime-start').filter({hasText:'核对原创建'}).waitFor();
    const marker=await page.evaluate(()=>sessionStorage.getItem('fitness-create-'+document.body.dataset.userId));assert(marker);
    await page.reload();await page.locator('#realtime-start').filter({hasText:'核对原创建'}).waitFor();assert.equal(await page.evaluate(()=>fixtureCamera.calls),0);
    await page.locator('#realtime-start').click();
    if(scenario==='create_not_sent')await page.getByRole('button',{name:'重试原创建',exact:true}).click();
    await page.waitForFunction(()=>Boolean(document.querySelector('#camera-preparation').dataset.sessionId));
    if(scenario==='create_not_sent'){assert.equal(requestKeys[0],requestKeys[1]);assert.deepEqual(requestBodies[0],requestBodies[1])}
    assert.equal(await page.locator('#camera-preparation').getAttribute('data-session-id'),original);assert.equal(createRequests,scenario==='create_not_sent'?2:1);assert.equal(await page.evaluate(()=>fixtureCamera.calls),0);
    const recovered=await snapshot(original);assert.equal(recovered.status,'ready');assert.equal(recovered.started_at_ms,null);
    const stored=JSON.parse(fixture.seed("import os,json\nfrom fitness_app.database import Database\nfrom sqlalchemy import select,func\nfrom fitness_app.db_schema import analysis_sessions\ndb=Database(os.environ['FITNESS_DATABASE_PATH'])\nwith db.transaction(False) as c: print(json.dumps({'sessions':c.scalar(select(func.count()).select_from(analysis_sessions))}))\ndb.dispose()"));assert.equal(stored.sessions,1);
    assert.equal(await page.evaluate(()=>sessionStorage.getItem('fitness-create-'+document.body.dataset.userId)),null);assert.deepEqual(errors,[]);
    console.log(JSON.stringify({browser:await context.browser().version(),scenario,createRequests,persistentSessions:1,autoCameraAfterReload:0,checks:'passed'}));
  }else{
  await page.locator('#realtime-start').click();await page.waitForURL('**/realtime/*');await page.locator('#live-state').filter({hasText:'分析中'}).waitFor();
  const sid=new URL(page.url()).pathname.split('/').at(-1);ids.add(sid);
  assert.equal(creates,1);assert.equal(await page.evaluate(()=>fixtureCamera.calls),1,'开始应沿用本页已开的设备');
  let data=await snapshot(sid);assert.equal(data.status,'running');assert.equal(data.total_count,0);assert.equal(data.counting_available,false);assert.equal(await page.locator('#live-amount').innerText(),'—');
  assert(!await page.content().then(html=>html.includes(lastControlToken)));assert.equal(await page.evaluate(()=>JSON.stringify(localStorage).includes('control_token')),false);
  if(scenario==='capture_failed'){
    fault(true);await page.locator('#live-pause').click();await page.locator('#live-save-failure').waitFor();
    await page.locator('#live-state').filter({hasText:'分析已冻结'}).waitFor();
    await page.screenshot({path:path.join(evidence,'save-failure.png'),fullPage:true});fault(false);
  }
  if(!compatibility&&include('pause_fault')){
    fault(true);await page.locator('#live-pause').click();await page.locator('#live-save-failure').waitFor();
    assert.equal(await page.evaluate(()=>fixtureCamera.streams.every(stream=>stream.getTracks().every(track=>track.readyState==='ended'))),true);
    data=await snapshot(sid);assert.equal(data.save_state,'save_failed');assert.equal(data.total_count,0);
    await page.evaluate(()=>{const note=document.createElement('p');note.className='notice';note.textContent='验证环境：MIT 视频单帧回放，仅验证状态与故障联动，未接入动作计数。';document.querySelector('#camera-preparation').prepend(note)});
    await page.screenshot({path:path.join(evidence,'save-failure.png'),fullPage:true});
    fault(false);await page.locator('#live-retry-save').click();await page.locator('#live-state').filter({hasText:'已暂停'}).waitFor();
    assert.equal((await snapshot(sid)).status,'paused');await page.locator('#live-resume').click();await page.locator('#live-state').filter({hasText:'分析中'}).waitFor();
    assert.equal(creates,1,'恢复不能新建训练记录');assert.equal((await snapshot(sid)).total_count,0);
  }
  if(!compatibility&&include('readonly')){
    await page.locator('#live-pause').click();await page.locator('#live-state').filter({hasText:'已暂停'}).waitFor();
    const reader=await context.newPage();reader.on('pageerror',error=>errors.push(error.message));await reader.goto(fixture.origin+'/realtime/'+sid);await reader.locator('#live-controller-note').filter({hasText:'本页只读'}).waitFor();
    assert.equal(await reader.evaluate(()=>fixtureCamera.calls),0);assert.equal(await reader.locator('#live-end').isDisabled(),true);
    await reader.locator('#live-claim').click();await reader.waitForFunction(()=>document.querySelector('#confirm-dialog').open||document.querySelector('#live-controller-note').textContent.includes('本页持有控制'));if(await reader.locator('#confirm-dialog').isVisible())await reader.getByRole('button',{name:'接管训练',exact:true}).click();
    await reader.locator('#live-controller-note').filter({hasText:'本页持有控制'}).waitFor();assert.equal(await reader.evaluate(()=>fixtureCamera.calls),0);
    await reader.close();await page.bringToFront();await page.locator('#live-controller-note').filter({hasText:'本页只读'}).waitFor();
    await page.locator('#live-claim').click();await page.waitForFunction(()=>document.querySelector('#confirm-dialog').open||document.querySelector('#live-controller-note').textContent.includes('本页持有控制'));if(await page.locator('#confirm-dialog').isVisible())await page.getByRole('button',{name:'接管训练',exact:true}).click();
    await page.locator('#live-controller-note').filter({hasText:'本页持有控制'}).waitFor();
    await page.locator('#live-resume').click();await page.locator('#live-state').filter({hasText:'分析中'}).waitFor();
  }
  if(!compatibility&&include('heartbeat')){
    let held=null,notify;const observed=new Promise(resolve=>notify=resolve);
    await page.route('**/api/v1/sessions/*/heartbeat',async route=>{if(!held){held=route;notify()}else await route.continue()});
    await Promise.race([observed,new Promise((resolve,reject)=>setTimeout(()=>reject(new Error('未取得待验证心跳')),6000))]);
    await page.locator('#live-pause').click();await page.locator('#live-state').filter({hasText:'已暂停'}).waitFor();
    const stale=page.waitForResponse(response=>response.url().endsWith('/heartbeat')&&response.status()===409);await held.continue();await stale;
    await page.locator('#live-controller-note').filter({hasText:'本页持有控制'}).waitFor();
    await page.locator('#live-resume').click();await page.locator('#live-state').filter({hasText:'分析中'}).waitFor();
    assert.equal((await snapshot(sid)).status,'running');await page.unroute('**/api/v1/sessions/*/heartbeat');
  }
  if(!compatibility&&include('background')){
    const before=await page.evaluate(()=>fixtureCamera.calls);
    await page.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,get:()=>true});document.dispatchEvent(new Event('visibilitychange'))});
    for(let attempt=0;attempt<50;attempt++){data=await snapshot(sid);if(data.status==='paused')break;await page.waitForTimeout(100)}
    assert.equal(data.status,'paused');assert.equal(await page.evaluate(()=>fixtureCamera.streams.every(stream=>stream.getTracks().every(track=>track.readyState==='ended'))),true);
    await page.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,get:()=>false});document.dispatchEvent(new Event('visibilitychange'))});
    await page.locator('#live-state').filter({hasText:'已暂停'}).waitFor();assert.equal(await page.evaluate(()=>fixtureCamera.calls),before);
    await page.locator('#live-resume').click();await page.locator('#live-state').filter({hasText:'分析中'}).waitFor();assert.equal(await page.evaluate(()=>fixtureCamera.calls),before+1);
  }
  if(!compatibility&&include('discard')){
    fault(true);await page.locator('#live-pause').click();await page.locator('#live-save-failure').waitFor();
    await page.locator('#live-discard').click();await page.locator('#confirm-dialog').waitFor();await page.getByRole('button',{name:'取消',exact:true}).click();assert.equal((await snapshot(sid)).save_state,'save_failed');
    await page.locator('#live-discard').click();await page.getByRole('button',{name:'确认放弃未保存部分',exact:true}).click();await page.waitForURL('**/records?discarded=pending');
    assert.match(await page.locator('#records-return-message').innerText(),/终态尚未写入/);data=await snapshot(sid);assert.equal(data.unsaved_discarded,true);assert.equal(data.total_count,0);fault(false);
  }
  if(!compatibility&&include('discard_leave')){
    fault(true);await page.locator('#live-pause').click();await page.locator('#live-save-failure').waitFor();
    await page.route('**/api/v1/sessions/*/heartbeat',route=>route.abort('failed'));
    for(let attempt=0;attempt<80;attempt++){data=await snapshot(sid);if(!data.control.has_controller)break;await page.waitForTimeout(250)}
    assert.equal(data.control.has_controller,false);await page.locator('#live-controller-note').filter({hasText:'本页只读'}).waitFor();
    await page.getByRole('link',{name:'首页',exact:true}).click();await page.getByRole('button',{name:'取消',exact:true}).click();assert(new URL(page.url()).pathname.startsWith('/realtime/'));assert.equal((await snapshot(sid)).save_state,'save_failed');
    await page.getByRole('link',{name:'首页',exact:true}).click();await page.getByRole('button',{name:'继续放弃确认',exact:true}).click();await page.getByRole('button',{name:'确认放弃未保存部分',exact:true}).click();await page.waitForURL(fixture.origin+'/');
    data=await snapshot(sid);assert.equal(data.unsaved_discarded,true);assert.equal(data.terminal_persisted,false);fault(false);
  }
  if(!compatibility&&await page.locator('#camera-preparation').count()){
    await page.evaluate(()=>{if(document.querySelector('#fixture-flow-note'))return;const note=document.createElement('p');note.id='fixture-flow-note';note.className='notice';note.textContent='验证环境：MIT 视频单帧回放，仅验证状态与故障联动，未接入动作计数。';document.querySelector('#camera-preparation').prepend(note)});
    await page.evaluate(()=>scrollTo(0,0));await page.waitForTimeout(220);await page.screenshot({path:path.join(evidence,'realtime.png'),fullPage:true});
  }
  if(!compatibility&&(include('end_unknown')||include('readonly'))){
    let lost=false;await page.route('**/api/v1/sessions/*/commands',async route=>{const body=route.request().postDataJSON();if(body.command==='end'&&!lost){lost=true;const result=await route.fetch();assert.equal(result.status(),200);await route.abort('failed')}else await route.continue()});
    await page.locator('#live-end').click();await page.getByRole('button',{name:'结束并保存',exact:true}).last().click();await page.waitForURL('**/records/*');
    assert(lost);assert.equal(ends,1,'丢响应后应先查询，不自动另发结束');data=await snapshot(sid);assert.equal(data.status,'ended_early');assert.equal(data.total_count,0);
  }else if(compatibility){await page.locator('#live-pause').click();await page.locator('#live-state').filter({hasText:'已暂停'}).waitFor()}
  assert.deepEqual(errors,[]);assert.equal(creates,1);
  console.log(JSON.stringify({browser:await context.browser().version(),compatibility,scenario,creates,endRequests:ends,counts:'unavailable, never fabricated',checks:'passed'}));
  }
}catch(error){if(page){const text=await page.locator('#live-error').textContent().catch(()=>null);console.log(JSON.stringify({diagnostic:text,errors,conflicts}));for(const id of ids){const data=await snapshot(id);console.log(JSON.stringify({state:data.status,save_state:data.save_state,control:data.control}))}}throw error}
finally{
  fault(false);
  if(context){for(const id of ids){const data=await snapshot(id);if(data?.save_state==='save_failed')await post('/api/v1/sessions/'+id+'/commands',{command:'retry_save',expected_version:data.state_version})}}
  await fixture.close();
}
