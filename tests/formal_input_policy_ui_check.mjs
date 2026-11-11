// 19：只检查新输入策略的实际预览坐标显示；公开原片静帧，不开始训练或重复持续性能。
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {execFileSync} from 'node:child_process';
import {BrowserFixture,root} from './formal_browser_support.mjs';

const output=path.join(root,'data/verification/step-19/input-policy-preview.json');
const names=['pose_input.py','preview.py','preview_routes.py','pose.py','frame_pipeline.py','content_bounds.py','static/camera.js','static/app.css','templates/prepare.html'];
const code=Object.fromEntries(names.map(name=>[name,crypto.createHash('sha256').update(fs.readFileSync(path.join(root,'fitness_app',name))).digest('hex')]));
const source=JSON.parse(fs.readFileSync(path.join(root,'data/datasets/development-movements/sources.json'),'utf8')).sources.find(s=>s.id==='commons-jumping-jacks-segment');
const identity={code,model:crypto.createHash('sha256').update(fs.readFileSync(path.join(root,'data/models/pose_landmarker_full.task'))).digest('hex'),
  source:crypto.createHash('sha256').update(fs.readFileSync(path.join(root,source.path))).digest('hex'),
  browser:execFileSync('powershell.exe',['-NoProfile','-Command',"(Get-Item -LiteralPath 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe').VersionInfo.ProductVersion"],{cwd:root,windowsHide:true,encoding:'utf8'}).trim()};
assert.equal(identity.source,source.sha256);
assert.equal(identity.model,JSON.parse(fs.readFileSync(path.join(root,'data/models/manifest.json'),'utf8')).sha256);
const previous=fs.existsSync(output)?JSON.parse(fs.readFileSync(output,'utf8')):null;
if((previous?.ui_assertions_passed===true||previous?.checks==='passed')&&JSON.stringify(previous.identity)===JSON.stringify(identity)){
  console.log(JSON.stringify({cache_hit:true,ui_assertions_passed:true,cleanup:previous.cleanup?.status??'completed',scope:'no camera replay or model rerun'}));
}else{
  const fixture=new BrowserFixture('ui-step19-performance');const errors=[];let latest,shuttingDown=false;
  try{
    fixture.env.FITNESS_PERFORMANCE_ACTION='jumping_jack';
    fixture.seed(`import os
from fitness_app import create_app
from fitness_app.auth import initialize_admin
app=create_app({'TESTING':True,'DATABASE_PATH':os.environ['FITNESS_DATABASE_PATH']})
db=app.extensions['database'];initialize_admin(db,'Preview_Admin','preview-fixture-password-123');db.dispose()`);
    await fixture.startTestService('tests/formal_performance_service.py');
    const context=await fixture.browser('Edge',{width:1280,height:900});
    await fixture.apiLogin(context,'Preview_Admin','preview-fixture-password-123');
    await context.addInitScript(()=>{
      Object.defineProperty(navigator.mediaDevices,'getUserMedia',{value:async constraints=>{
        if(constraints.audio!==false)throw Error('静帧预览禁止麦克风');
        const video=document.createElement('video');video.muted=true;video.preload='auto';video.src='/__fixture/performance-source';
        await new Promise((resolve,reject)=>{video.onloadeddata=resolve;video.onerror=reject});
        const canvas=document.createElement('canvas');canvas.width=1280;canvas.height=720;
        const draw=()=>{const ctx=canvas.getContext('2d');ctx.fillStyle='black';ctx.fillRect(0,0,1280,720);ctx.drawImage(video,160,0,960,720)};
        draw();const stream=canvas.captureStream(30),timer=setInterval(draw,33),track=stream.getVideoTracks()[0],stop=track.stop.bind(track);
        Object.defineProperty(track,'stop',{value:()=>{clearInterval(timer);video.pause();stop()}});return stream;
      }});
      Object.defineProperty(navigator.mediaDevices,'enumerateDevices',{value:async()=>[{kind:'videoinput',deviceId:'public-static-frame',label:'公开素材静帧 · 非现场相机'}]});
    });
    const page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));
    page.on('response',async response=>{try{if(!shuttingDown&&response.request().method()==='GET'&&/\/api\/v1\/previews\/[a-f0-9-]+$/.test(new URL(response.url()).pathname)&&response.ok()){
      const result=(await response.json()).data?.result;if(result)latest=result;
    }}catch(error){if(!shuttingDown)errors.push(error.message)}});
    await page.goto(fixture.origin+'/prepare?action=jumping_jack');await page.locator('#camera-toggle').click();
    await page.waitForFunction(()=>!document.querySelector('#realtime-start').disabled,{},{timeout:20000});
    assert(latest?.framing.ready_for_tracking);assert.equal(latest.model_version,'mediapipe-full-float16-1-content-roi-1');
    assert.deepEqual(latest.input_transform.original_size,[1280,720]);
    const [left,top,right,bottom]=latest.input_transform.crop_pixels;
    assert(left>=152&&left<=168&&top>=82&&top<=98&&right>=1112&&right<=1128&&bottom>=622&&bottom<=638);
    assert(latest.framing.landmarks[11].x>.45&&latest.framing.landmarks[11].x<.56);
    assert.equal(await page.locator('#camera-canvas').getAttribute('width'),'1280');
    await page.evaluate(()=>window.scrollTo({top:0,behavior:'instant'}));
    await page.screenshot({path:output.replace('.json','.png'),fullPage:true});
    const sessions=Number(fixture.seed(`import os,sqlite3
from pathlib import Path
with sqlite3.connect(Path(os.environ['FITNESS_DATABASE_PATH']).as_uri()+'?mode=ro',uri=True) as c: print(c.execute('SELECT COUNT(*) FROM analysis_sessions').fetchone()[0])`).trim());
    assert.equal(sessions,0);assert.deepEqual(errors,[]);
    fs.writeFileSync(output,JSON.stringify({checks:'passed',scope:'public source static-frame preview; no training session, physical camera or sustained-performance run',
      browser:await context.browser().version(),identity,model:latest.model_version,input_transform:latest.input_transform,sessions},null,2)+'\n');
    console.log(JSON.stringify({checks:'passed',input_transform:latest.input_transform,sessions}));
  }finally{shuttingDown=true;await fixture.close()}
}
