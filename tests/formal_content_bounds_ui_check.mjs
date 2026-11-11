// 新增留边掩码界面连接：实际JPEG + 已有模型输出，不能称为新SDK或现场相机验收。
import assert from 'node:assert/strict';
import path from 'node:path';
import {BrowserFixture,root} from './formal_browser_support.mjs';

const fixture=new BrowserFixture('ui-step14-bounds');const errors=[];
try{
  await fixture.startTestService('tests/formal_content_bounds_service.py');const context=await fixture.browser('Edge',{width:1280,height:900});
  await fixture.apiLogin(context,'Bounds_Admin','bounds-fixture-password-123');
  await context.addInitScript(()=>{
    Object.defineProperty(navigator.mediaDevices,'getUserMedia',{value:async()=>{
      const image=new Image();image.src='/__fixture/content-bounds.jpg';await image.decode();const source=document.createElement('canvas');source.width=324;source.height=480;
      const draw=()=>source.getContext('2d').drawImage(image,0,0);draw();const stream=source.captureStream(30),timer=setInterval(draw,33),track=stream.getVideoTracks()[0],stop=track.stop.bind(track);
      Object.defineProperty(track,'getSettings',{value:()=>({width:324,height:480,frameRate:30,deviceId:'bounds-replay'})});
      Object.defineProperty(track,'stop',{value:()=>{clearInterval(timer);stop()}});return stream;
    }});
    Object.defineProperty(navigator.mediaDevices,'enumerateDevices',{value:async()=>[{kind:'videoinput',deviceId:'bounds-replay',label:'裁切反例回放 · 非现场相机'}]});
  });
  let pid;const page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));
  page.on('response',async response=>{if(response.request().method()==='POST'&&new URL(response.url()).pathname==='/api/v1/previews'&&response.status()===201)pid=(await response.json()).data.preview_id});
  await page.goto(fixture.origin+'/prepare?action=barbell_overhead_press');await page.locator('#camera-toggle').click();
  await page.locator('#camera-message').filter({hasText:'必要部位不可见'}).waitFor();assert(pid);
  const value=(await(await context.request.get(fixture.origin+'/api/v1/previews/'+pid)).json()).data;
  assert.equal(value.result.framing.code,'MISSING_PARTS');assert.equal(value.result.framing.ready_for_tracking,false);
  assert.equal(value.result.framing.landmarks[31].valid,false);assert(value.result.framing.outside_content_indices.includes(31));
  await page.waitForFunction(()=>document.querySelector('#submission-settings').textContent.includes('可用 0.0 fps'));
  assert(await page.locator('#realtime-start').isDisabled());assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));assert.deepEqual(errors,[]);
  await page.evaluate(()=>{const note=document.createElement('p');note.className='notice';note.textContent='工程验证：实际裁切图像＋已缓存关键点，仅检查图像边界；不是模型性能测量。';document.querySelector('#camera-preparation').prepend(note)});
  await page.evaluate(()=>window.scrollTo({top:0,behavior:'instant'}));await page.screenshot({path:path.join(root,'data/verification/step-14/content-bounds-preview.png'),fullPage:true});
  await page.locator('#camera-toggle').click();await page.locator('#camera-message').filter({hasText:'摄像头已关闭'}).waitFor();
  console.log(JSON.stringify({checks:'passed',browser:await context.browser().version(),scope:'actual cropped JPEG and cached raw SDK landmarks',freshPoseInference:false,framing:value.result.framing.code,outside:value.result.framing.outside_content_indices}));
}finally{await fixture.close()}
