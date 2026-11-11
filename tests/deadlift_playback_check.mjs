// 只诊断公开源片的隐藏循环播放；不打开相机、姿态预览或分析会话。
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import assert from 'node:assert/strict';
import {BrowserFixture,root} from './formal_browser_support.mjs';

const durationMs=220000,output=path.join(root,'data/verification/step-19/deadlift-playback.json');
assert(!fs.existsSync(output),'已有诊断，先读取记录，不重复播放');
const fixture=new BrowserFixture('ui-step19-deadlift');let page;
try{
  fixture.env.FITNESS_PERFORMANCE_ACTION='conventional_deadlift';
  fixture.seed(`import os
from fitness_app import create_app
from fitness_app.auth import initialize_admin
app=create_app({'TESTING':True,'DATABASE_PATH':os.environ['FITNESS_DATABASE_PATH']})
db=app.extensions['database'];initialize_admin(db,'Playback_Admin','playback-fixture-password-123');db.dispose()`);
  await fixture.startTestService('tests/formal_performance_service.py');
  const context=await fixture.browser('Edge',{width:1280,height:900});
  await fixture.apiLogin(context,'Playback_Admin','playback-fixture-password-123');
  await context.route('**/__fixture/player-probe',route=>route.fulfill({contentType:'text/html',body:'<!doctype html><title>Public source playback probe</title>'}));
  page=await context.newPage();let poseRequests=0;
  page.on('request',r=>{if(/\/api\/v1\/(previews|sessions)(\/|$)/.test(new URL(r.url()).pathname))poseRequests++});
  await page.goto(fixture.origin+'/__fixture/player-probe');
  await page.evaluate(async()=>{
    const source=document.createElement('video');source.muted=true;source.playsInline=true;source.preload='auto';source.loop=true;source.src='/__fixture/performance-source';
    await new Promise((resolve,reject)=>{source.onloadeddata=resolve;source.onerror=reject});
    const canvas=document.createElement('canvas');canvas.width=1280;canvas.height=720;
    const paint=()=>{const ctx=canvas.getContext('2d'),scale=Math.min(1280/source.videoWidth,720/source.videoHeight),w=source.videoWidth*scale,h=source.videoHeight*scale;ctx.fillStyle='black';ctx.fillRect(0,0,1280,720);ctx.drawImage(source,(1280-w)/2,(720-h)/2,w,h)};
    paint();const stream=canvas.captureStream(30),consumer=document.createElement('video');consumer.hidden=true;consumer.muted=true;consumer.srcObject=stream;document.body.append(consumer);await consumer.play();
    const small=document.createElement('canvas');small.width=32;small.height=32;const ctx=small.getContext('2d',{willReadFrequently:true});
    const state=window.playbackProbe={source,stream,consumer,timers:[],begin:performance.now(),frames:0,loops:0,lastMedia:-1,lastCallback:performance.now(),maxCallbackGap:0,lastPixelChange:performance.now(),lastHash:null,maxPixelHold:0,windows:[]};
    function decoded(now,metadata){state.frames++;if(metadata.mediaTime<state.lastMedia)state.loops++;state.lastMedia=metadata.mediaTime;state.maxCallbackGap=Math.max(state.maxCallbackGap,now-state.lastCallback);state.lastCallback=now;source.requestVideoFrameCallback(decoded)}
    source.requestVideoFrameCallback(decoded);
    state.timers.push(setInterval(paint,33));
    state.timers.push(setInterval(()=>{ctx.drawImage(source,0,0,32,32);const data=ctx.getImageData(0,0,32,32).data;let h=2166136261;for(const byte of data)h=Math.imul(h^byte,16777619);const now=performance.now();if(h!==state.lastHash){state.lastHash=h;state.lastPixelChange=now}state.maxPixelHold=Math.max(state.maxPixelHold,now-state.lastPixelChange)},250));
    await source.play();state.begin=performance.now();
  });
  for(let elapsed=0;elapsed<durationMs;){
    const next=Math.min(30000,durationMs-elapsed);await page.waitForTimeout(next);elapsed+=next;
    const sample=await page.evaluate(()=>{const p=playbackProbe,v=p.source,q=v.getVideoPlaybackQuality();const s={elapsed_ms:Math.round(performance.now()-p.begin),media_time:v.currentTime,paused:v.paused,ended:v.ended,ready_state:v.readyState,decoded_callbacks:p.frames,loops:p.loops,total_video_frames:q.totalVideoFrames,dropped_video_frames:q.droppedVideoFrames,callback_idle_ms:Math.round(performance.now()-p.lastCallback),pixel_hold_ms:Math.round(performance.now()-p.lastPixelChange),max_pixel_hold_ms:Math.round(p.maxPixelHold)};p.windows.push(s);return s});
    console.log(JSON.stringify(sample));
  }
  const result=await page.evaluate(()=>{const p=playbackProbe;for(const timer of p.timers)clearInterval(timer);p.source.pause();p.consumer.pause();for(const t of p.stream.getTracks())t.stop();return {duration_seconds:(performance.now()-p.begin)/1000,decoded_callbacks:p.frames,loops:p.loops,max_callback_gap_ms:p.maxCallbackGap,max_pixel_hold_ms:p.maxPixelHold,samples:p.windows}});
  assert.equal(poseRequests,0);
  fs.writeFileSync(output,JSON.stringify({scope:'220-second public portrait MP4 hidden playback with canvas capture consumer; no pose model or analysis session; diagnostic comparison only',source_sha256:crypto.createHash('sha256').update(fs.readFileSync(path.join(root,'data/datasets/mydeadlift/Gerakan Benar/GB_S02_R29.mp4'))).digest('hex'),browser:await context.browser().version(),pose_requests:poseRequests,...result},null,2)+'\n');
}finally{await fixture.close()}
