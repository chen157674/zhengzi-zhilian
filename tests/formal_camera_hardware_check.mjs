// 19现场设备检查：必须先获本聊天用户明确授权，再传--allow-camera；不截图、录像或上传画面。
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {BrowserFixture,root} from './formal_browser_support.mjs';

if(!process.argv.includes('--allow-camera')){
  console.log('未开启摄像头。需用户明确授权15秒本机取景检查后才使用--allow-camera；仅保留聚合指标。');
}else{
  const fixture=new BrowserFixture('ui-step19-hardware');let context,page,report;
  try{
    fixture.seed(`
import os
from fitness_app import create_app
from fitness_app.auth import initialize_admin
app=create_app({'TESTING':True,'DATABASE_PATH':os.environ['FITNESS_DATABASE_PATH']})
db=app.extensions['database'];initialize_admin(db,'Camera_Admin','camera-fixture-password-123');db.dispose()
`);fixture.start();context=await fixture.browser('Edge',{width:1280,height:900});await fixture.apiLogin(context,'Camera_Admin','camera-fixture-password-123');
    await context.route('**/*',route=>new URL(route.request().url()).origin===fixture.origin?route.continue():route.abort());
    await context.grantPermissions(['camera'],{origin:fixture.origin});
    await context.addInitScript(()=>{
      window.hardwareCheck={calls:0,tracks:[],settings:null,shown:0,usable:0,first:null,last:null,lastSeq:null,openedAt:null,stoppedAt:null};
      const original=navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
      navigator.mediaDevices.getUserMedia=async constraints=>{
        if(constraints.audio!==false)throw new Error('检查禁止麦克风');
        if(hardwareCheck.calls!==0)throw new Error('本次授权只允许开启一次');
        hardwareCheck.calls++;const stream=await original(constraints);hardwareCheck.openedAt=performance.now();hardwareCheck.tracks.push(...stream.getTracks());
        for(const track of stream.getTracks()){const stop=track.stop.bind(track);track.stop=()=>{stop();hardwareCheck.stoppedAt??=performance.now()}}
        // 提前1秒停止，为自动化命令和关闭检查留出余量。
        setTimeout(()=>stream.getTracks().forEach(track=>track.stop()),14000);
        const {width,height,frameRate}=stream.getVideoTracks()[0].getSettings();hardwareCheck.settings={width,height,frameRate};return stream;
      };
      document.addEventListener('DOMContentLoaded',()=>{
        const canvas=document.querySelector('#camera-canvas');if(!canvas)return;
        new MutationObserver(()=>{
          const seq=Number(canvas.dataset.frameSeq),stamp=Number(canvas.dataset.capturedMs);
          if(!Number.isFinite(seq)||!Number.isFinite(stamp)||seq===hardwareCheck.lastSeq)return;
          hardwareCheck.lastSeq=seq;hardwareCheck.shown++;if(canvas.dataset.framingUsable==='1')hardwareCheck.usable++;
          hardwareCheck.first??=stamp;hardwareCheck.last=stamp;
        }).observe(canvas,{attributes:true,attributeFilter:['data-frame-seq']});
      });
    });
    page=await context.newPage();await page.goto(fixture.origin+'/prepare?action=bodyweight_squat');
    const begin=Date.now();await page.locator('#camera-toggle').click();
    // 限时15秒；只读取计数器和设备参数，不读取／输出画面、关键点、设备ID或名称。
    while(Date.now()-begin<15000){
      const state=await page.evaluate(()=>({opened:hardwareCheck.tracks.some(t=>t.readyState==='live'),message:document.querySelector('#camera-message').textContent}));
      if(!state.opened&&/没有找到|授权被拒绝|无法读取/.test(state.message))break;
      await page.waitForTimeout(500);
    }
    const aggregate=await page.evaluate(()=>({calls:hardwareCheck.calls,settings:hardwareCheck.settings,displayedFrames:hardwareCheck.shown,usableFrames:hardwareCheck.usable,
      sourceSpanMs:hardwareCheck.first===null?0:hardwareCheck.last-hardwareCheck.first,framingMessage:document.querySelector('#camera-message').textContent}));
    if(await page.locator('#camera-toggle').getAttribute('data-action')==='camera.close')await page.locator('#camera-toggle').click();
    await page.waitForFunction(()=>hardwareCheck.tracks.every(t=>t.readyState==='ended'));
    const cameraOpenMs=await page.evaluate(()=>hardwareCheck.openedAt===null?null:hardwareCheck.stoppedAt-hardwareCheck.openedAt);
    if(cameraOpenMs!==null)assert(cameraOpenMs<=15000,'实际取景超过授权上限');
    const records=(await(await context.request.get(fixture.origin+'/api/v1/records')).json()).data;assert.equal(records.total,0);
    report={checks:aggregate.displayedFrames>0?'hardware_transport_passed':'hardware_not_confirmed',scope:'15-second real camera preview only; no full exercise or accuracy acceptance',
      ...aggregate,cameraOpenMs,tracksReleased:true,externalNetworkBlocked:true,trainingRecordsCreated:0,videoOrScreenshotSaved:false,rawFramesOrLandmarksExported:false};
  }finally{
    // 即使取景／接口失败也先结束本测试流，再关闭专属浏览器、服务和临时资料。
    if(page&&!page.isClosed())await page.evaluate(()=>{for(const track of window.hardwareCheck?.tracks||[])track.stop()}).catch(()=>{});
    await fixture.close();
  }
  if(report){const out=path.join(root,'data/verification/step-19/camera-hardware.json');fs.writeFileSync(out,JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify(report))}
}
