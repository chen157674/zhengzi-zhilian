// 09 表单／分组／休息 UI。合成姿态和已知事件只证明业务链，不证明识别效果。
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {BrowserFixture,root} from './formal_browser_support.mjs';

const fixture=new BrowserFixture('ui-step09'),evidence=path.join(root,'第四轮优化/实现核对');fs.mkdirSync(evidence,{recursive:true});
const fromRest=process.argv.includes('--from-rest');
const visualOnly=process.argv.includes('--visual-only');
let context,page,sid=null,controlToken=null,offset=0;const errors=[],creates=[];
async function screenshot(name){await page.evaluate(()=>window.scrollTo({top:0,behavior:'instant'}));await page.waitForFunction(()=>window.scrollY===0);await page.screenshot({path:path.join(evidence,name),fullPage:true})}
async function snapshot(){return (await(await context.request.get(fixture.origin+'/api/v1/sessions/'+sid)).json()).data}
async function post(url,body){const csrf=(await(await context.request.get(fixture.origin+'/api/v1/auth/csrf')).json()).data.csrf_token;return context.request.post(fixture.origin+url,{data:body,headers:{'X-CSRFToken':csrf,'Idempotency-Key':crypto.randomUUID()}})}
async function advance(seconds){
  while(seconds>0){
    const data=await snapshot();await post(`/api/v1/sessions/${sid}/heartbeat`,{control_token:controlToken,controller_epoch:data.control.controller_epoch,stream_epoch:data.control.stream_epoch});
    const step=Math.min(10,seconds);offset+=step;seconds-=step;
    const file=path.join(fixture.temporary,'clock.json');fs.writeFileSync(file+'.new',JSON.stringify({offset}));
    // Windows 的短暂读句柄可能阻止替换；保留原子发布，避免服务读取半份 JSON。
    for(let attempt=0;;attempt++){
      try{fs.renameSync(file+'.new',file);break}
      catch(error){if(!['EPERM','EBUSY','EACCES'].includes(error.code)||attempt>=20)throw error;await page.waitForTimeout(25)}
    }
    await page.waitForTimeout(150);
  }
}
try{
  await fixture.startTestService('tests/formal_targets_service.py');context=await fixture.browser('Edge',{width:1440,height:1000});await fixture.apiLogin(context,'Target_User','target-fixture-password-123');
  await context.addInitScript(()=>{
    window.fixtureCamera={calls:0,streams:[]};
    Object.defineProperty(navigator.mediaDevices,'getUserMedia',{value:async()=>{fixtureCamera.calls++;const canvas=document.createElement('canvas');canvas.width=640;canvas.height=480;const ctx=canvas.getContext('2d');const draw=()=>{ctx.fillStyle='#243a31';ctx.fillRect(0,0,640,480);ctx.fillStyle='#dce8df';ctx.font='18px sans-serif';ctx.fillText('D-POSE / D-EVENT 业务验证输入',18,28)};draw();const stream=canvas.captureStream(30),timer=setInterval(draw,33),track=stream.getVideoTracks()[0],stop=track.stop.bind(track);Object.defineProperty(track,'stop',{value:()=>{clearInterval(timer);stop()}});Object.defineProperty(track,'getSettings',{value:()=>({width:640,height:480,frameRate:30,deviceId:'synthetic'})});fixtureCamera.streams.push(stream);return stream}});
    Object.defineProperty(navigator.mediaDevices,'enumerateDevices',{value:async()=>[{kind:'videoinput',deviceId:'synthetic',label:'合成业务输入（非识别验证）'}]});
  });
  page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));
  page.on('response',async response=>{const url=new URL(response.url());if(response.request().method()==='POST'&&url.pathname==='/api/v1/sessions'&&response.status()===201){const data=(await response.json()).data;creates.push({id:data.session_id,body:response.request().postDataJSON()})}if(url.pathname.endsWith('/control')&&response.status()===200){const data=(await response.json()).data;if(data.control_token)controlToken=data.control_token}});
  for(const [action,label] of (fromRest?[['bodyweight_squat','每组次数']]:[['standing_dumbbell_curl','每侧次数'],['forearm_plank','保持时间（秒）'],['bodyweight_squat','每组次数']])){
    await page.goto(fixture.origin+'/prepare?action='+action);await page.locator('#prepare-settings>summary').click();await page.locator('input[name="training-mode"][value="target"]').check();assert.equal(await page.locator('[for="target-value"]').innerText(),label);assert.equal(await page.locator('#target-value').inputValue(),'');
  }
  await page.locator('#camera-toggle').click();await page.waitForFunction(()=>!document.querySelector('#realtime-start').disabled);
  if(!fromRest){
  await page.locator('#target-sets').fill('1000');await page.locator('#target-value').fill('1.5');await page.locator('#target-rest').fill('3');
  await page.locator('#realtime-start').click();await page.locator('#target-sets-error').waitFor();assert.equal(await page.locator('#target-sets').inputValue(),'1000');assert.equal(await page.evaluate(()=>document.activeElement.id),'target-sets');assert.equal(creates.length,0);
  await page.locator('#target-sets').fill('2');await page.locator('#target-value').fill('1');
  await page.locator('input[name="training-mode"][value="free"]').check();assert.equal(await page.locator('#target-fields').isHidden(),true);
  let rejectControl=true;await page.route('**/api/v1/sessions/*/control',async route=>{if(rejectControl){rejectControl=false;return route.fulfill({status:409,contentType:'application/json',body:JSON.stringify({error:{code:'FIXTURE_CONFIG_ONLY',message:'该切片只检查自由模式配置'}})})}await route.continue()});
  await page.locator('#realtime-start').click();await page.locator('#live-error').filter({hasText:'只检查自由模式配置'}).waitFor();assert.equal(creates.length,1);assert.equal(creates[0].body.target,null);assert.equal(creates[0].body.mode,'free');
  console.log(JSON.stringify({scope:'target-form-and-free-config',checks:'passed'}));
  }
  await page.locator('input[name="training-mode"][value="target"]').check();await page.locator('#target-sets').fill('2');await page.locator('#target-value').fill('1');await page.locator('#target-rest').fill('3');
  await screenshot('固定动作准备.png');
  await page.locator('#realtime-start').click();await page.waitForURL('**/realtime/*');sid=new URL(page.url()).pathname.split('/').at(-1);await page.locator('#live-state').filter({hasText:'休息中'}).waitFor();
  assert.equal(creates.length,fromRest?1:2);if(!fromRest)assert.notEqual(creates[0].id,creates[1].id);assert.equal(creates.at(-1).body.target.unit,'reps');
  let data=await snapshot();assert.equal(data.completed_sets,1);assert.equal(data.total_count,1);assert.equal(data.groups[0].status,'completed');
  const before=data.live_rest_remaining_ms;const extension=page.waitForResponse(response=>response.url().endsWith('/commands')&&response.request().postDataJSON()?.command==='extend_rest');await page.locator('#live-extend-rest').click();assert.equal((await extension).status(),200);
  data=await snapshot();assert(data.live_rest_remaining_ms>before+27000&&data.live_rest_remaining_ms<=before+30000);
  await page.locator('#live-pause').click();await page.locator('#live-state').filter({hasText:'已暂停'}).waitFor();const paused=(await snapshot()).rest_remaining_ms;const calls=await page.evaluate(()=>fixtureCamera.calls);
  await page.waitForTimeout(1100);assert.equal((await snapshot()).rest_remaining_ms,paused);
  await page.locator('#live-resume').filter({hasText:'继续休息'}).click();await page.locator('#live-state').filter({hasText:'休息中'}).waitFor();assert.equal(await page.evaluate(()=>fixtureCamera.calls),calls);
  await page.locator('#camera-message').filter({hasText:'当前未采集画面'}).waitFor();
  await screenshot('固定动作组间休息.png');
  if(!visualOnly){
  await advance(Math.ceil((await snapshot()).live_rest_remaining_ms/1000)+1);await page.locator('#live-state').filter({hasText:'等待目标'}).waitFor();
  await page.locator('#live-resume').filter({hasText:'重新检查取景'}).click();await page.locator('#live-state').filter({hasText:'已完成'}).waitFor();
  data=await snapshot();assert.equal(data.completed_sets,2);assert.equal(data.total_count,2);assert.equal(data.status,'completed');assert.equal(data.save_state,'clean');assert(data.groups.every(group=>group.status==='completed'));
  const records=(await(await context.request.get(fixture.origin+'/api/v1/records')).json()).data;assert.equal(records.total,1,'未开始的自由模式配置不算成果');assert.equal(records.records[0].session_id,sid);
  await page.locator('#live-result').click();await page.waitForURL('**/records/*');await page.locator('#record-tab-info').click();await page.locator('#detail-count').filter({hasText:'2 次'}).waitFor();assert.deepEqual(errors,[]);
  console.log(JSON.stringify({browser:await context.browser().version(),targetSessions:1,completedSets:2,totalCount:2,input:'D-POSE/D-EVENT/D-CLOCK only',checks:'passed'}));
  }else console.log(JSON.stringify({scope:'closed-camera-copy-and-two-screenshots',checks:'passed'}));
}catch(error){console.log(JSON.stringify({errors,diagnostic:page?await page.locator('#live-error').textContent({timeout:1000}).catch(()=>null):null}));throw error}
finally{await fixture.close()}
