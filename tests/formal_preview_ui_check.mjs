// 07 的新浏览器帧链路。输入为已下载训练视频的一帧回放，不是现场相机验收。
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {BrowserFixture,root} from './formal_browser_support.mjs';

const fixture=new BrowserFixture('ui-step07'),compatibility=process.argv.includes('--compat-only');
const displayOnly=process.argv.includes('--display-only');
const evidence=path.join(root,'data/verification/step-07');fs.mkdirSync(evidence,{recursive:true});
const prepared=JSON.parse(fixture.seed(`
import base64,json,os
from io import BytesIO
import av
from PIL import Image
from fitness_app import create_app
from fitness_app.auth import initialize_admin
from fitness_app.paths import ROOT
app=create_app({'TESTING':True,'DATABASE_PATH':os.environ['FITNESS_DATABASE_PATH']})
db=app.extensions['database'];initialize_admin(db,'Preview_Admin','preview-fixture-password-123');db.dispose()
with av.open(str(ROOT/'data/datasets/mydeadlift/Gerakan Benar/GB_S01_R01.mp4')) as container:
    frame=next(container.decode(video=0));image=Image.fromarray(frame.to_ndarray(format='rgb24'));image.thumbnail((1280,720));data=BytesIO();image.save(data,format='JPEG',quality=85)
print(json.dumps({'jpeg':base64.b64encode(data.getvalue()).decode(),'width':image.width,'height':image.height}))
`));
let context;
try{
  fixture.start();context=await fixture.browser(compatibility?'Chrome':'Edge',compatibility?{width:1280,height:900}:{width:1440,height:1000});
  await fixture.apiLogin(context,'Preview_Admin','preview-fixture-password-123');
  await context.route('**/__fixture/preview.jpg',route=>route.fulfill({contentType:'image/jpeg',body:Buffer.from(prepared.jpeg,'base64')}));
  await context.addInitScript(({width,height})=>{
    window.fixtureCamera={calls:0,stops:0,mode:'replay',constraints:[],streams:[]};
    Object.defineProperty(navigator.mediaDevices,'getUserMedia',{value:async constraints=>{
      const state=window.fixtureCamera;state.calls++;state.constraints.push(constraints);
      if(state.mode!=='replay')throw new DOMException('fixture device failure',state.mode);
      const image=new Image();image.src='/__fixture/preview.jpg';await image.decode();const canvas=document.createElement('canvas');canvas.width=width;canvas.height=height;
      const draw=()=>canvas.getContext('2d').drawImage(image,0,0,width,height);draw();const stream=canvas.captureStream(30);const interval=setInterval(draw,33);
      const track=stream.getVideoTracks()[0],stop=track.stop.bind(track),deviceId=constraints.video.deviceId?.exact||'fixture-one';
      Object.defineProperty(track,'getSettings',{value:()=>({width,height,frameRate:30,deviceId})});
      Object.defineProperty(track,'stop',{value:()=>{if(track.readyState!=='ended')state.stops++;clearInterval(interval);stop()}});
      state.streams.push(stream);return stream;
    }});
    Object.defineProperty(navigator.mediaDevices,'enumerateDevices',{value:async()=>[{kind:'videoinput',deviceId:'fixture-one',label:'回放输入 1（非现场相机）'},{kind:'videoinput',deviceId:'fixture-two',label:'回放输入 2（非现场相机）'}]});
  },{width:prepared.width,height:prepared.height});
  const page=await context.newPage(),errors=[],frames=[];let previewId=null;
  page.on('pageerror',error=>errors.push(error.message));
  page.on('response',async response=>{if(response.url().endsWith('/api/v1/previews')&&response.request().method()==='POST'&&response.status()===201)previewId=(await response.json()).data.preview_id});
  page.on('request',request=>{if(/\/previews\/[^/]+\/frames$/.test(request.url()))frames.push(request.url())});
  await page.goto(fixture.origin+'/prepare?action=conventional_deadlift');await page.locator('#camera-toggle').waitFor();
  assert.equal(await page.evaluate(()=>fixtureCamera.calls),0);assert.equal(frames.length,0);assert.equal(await page.locator('#realtime-start').isDisabled(),true);
  await page.locator('#camera-toggle').click();await page.locator('#camera-canvas').waitFor();await page.locator('#submission-settings').filter({hasText:'实际提交'}).waitFor();
  assert(previewId);const originalId=previewId;
  let snapshot=(await(await context.request.get(fixture.origin+'/api/v1/previews/'+previewId)).json()).data;
  assert.equal(snapshot.status,'active');assert.equal(snapshot.result.framing.person_count,1);assert.equal(snapshot.result.framing.landmarks.length,33);
  assert(snapshot.queue.pending<=1&&snapshot.queue.processing<=1);assert.match(await page.locator('#capture-settings').innerText(),/30.0 fps/);
  const calls=await page.evaluate(()=>fixtureCamera.constraints);assert.equal(calls[0].audio,false);assert.equal(calls[0].video.width.ideal,1280);
  const geometry=await page.locator('#camera-canvas').evaluate(canvas=>{const box=canvas.getBoundingClientRect(),stage=document.querySelector('#camera-stage').getBoundingClientRect(),style=getComputedStyle(canvas);return {inside:box.top>=stage.top&&box.left>=stage.left&&box.bottom<=stage.bottom&&box.right<=stage.right,fit:style.objectFit,transform:style.transform}});
  assert(geometry.inside,'预览不能超出容器裁切');assert.equal(geometry.fit,'contain');assert.match(geometry.transform,/-1/);
  await page.locator('#camera-mirror').uncheck();assert.equal(await page.locator('#camera-canvas').evaluate(canvas=>getComputedStyle(canvas).transform),'none');
  assert.equal(await page.locator('#realtime-start').isDisabled(),false);
  if(!compatibility&&!displayOnly){
    // 只在主浏览器验证设备切换、拒绝／占用／缺失和离开释放；不复制账号测试。
    await page.locator('#camera-device').selectOption('fixture-two');await page.locator('#camera-canvas').waitFor();
    await page.waitForFunction(()=>fixtureCamera.calls===2&&!document.querySelector('#camera-device').disabled);
    assert.equal(await page.evaluate(()=>fixtureCamera.stops),1);assert.notEqual(previewId,originalId);
    snapshot=(await(await context.request.get(fixture.origin+'/api/v1/previews/'+originalId)).json()).data;assert.equal(snapshot.status,'released');
    const note=await page.evaluate(()=>{const el=document.createElement('p');el.className='notice';el.id='fixture-note';el.textContent='验证输入：已下载训练视频单帧回放，非现场摄像头；骨架由本机模型实际计算。';document.querySelector('#camera-preparation').prepend(el);return true});assert(note);
    await page.evaluate(()=>scrollTo(0,0));await page.waitForTimeout(220);await page.screenshot({path:path.join(evidence,'preview.png'),fullPage:true});
  }
  if(displayOnly&&!compatibility){await page.evaluate(()=>{const el=document.createElement('p');el.className='notice';el.textContent='验证输入：已下载训练视频单帧回放，非现场摄像头；骨架由本机模型实际计算。';document.querySelector('#camera-preparation').prepend(el)});await page.evaluate(()=>scrollTo(0,0));await page.waitForTimeout(220);await page.screenshot({path:path.join(evidence,'preview.png'),fullPage:true})}
  await page.locator('#camera-toggle').click();await page.getByRole('button',{name:'开启摄像头',exact:true}).waitFor();
  assert.equal(await page.evaluate(()=>fixtureCamera.streams.every(stream=>stream.getTracks().every(track=>track.readyState==='ended'))),true);
  assert.equal(await page.locator('#camera-canvas').isHidden(),true);
  if(!compatibility&&!displayOnly){
    for(const [name,text] of [['NotAllowedError','授权被拒绝'],['NotReadableError','可能正被其他应用占用'],['NotFoundError','没有找到']]){
      await page.evaluate(name=>{fixtureCamera.mode=name},name);await page.locator('#camera-toggle').click();await page.locator('#camera-message').filter({hasText:text}).waitFor();assert.equal(await page.locator('#realtime-start').isDisabled(),true);
    }
    await page.evaluate(()=>{fixtureCamera.mode='replay'});await page.locator('#camera-toggle').click();await page.locator('#camera-canvas').waitFor();
    const lastId=previewId;await page.getByRole('link',{name:'← 返回动作选择',exact:true}).click();await page.waitForURL('**/actions');
    snapshot=(await(await context.request.get(fixture.origin+'/api/v1/previews/'+lastId)).json()).data;assert.equal(snapshot.status,'released');
  }
  assert.deepEqual(errors,[]);const records=(await(await context.request.get(fixture.origin+'/api/v1/records')).json()).data;assert.equal(records.total,0);
  const rows=JSON.parse(fixture.seed("import os,json\nfrom fitness_app.database import Database\nfrom fitness_app.db_schema import analysis_sessions\nfrom sqlalchemy import select,func\ndb=Database(os.environ['FITNESS_DATABASE_PATH'])\nwith db.transaction(False) as c: print(json.dumps({'sessions':c.scalar(select(func.count()).select_from(analysis_sessions))}))\ndb.dispose()"));assert.equal(rows.sessions,0);
  console.log(JSON.stringify({browser:await context.browser().version(),compatibility,displayOnly,input:'training single-frame replay, not onsite camera',landmarks:33,trainingRows:0,checks:'passed'}));
}finally{await fixture.close()}
