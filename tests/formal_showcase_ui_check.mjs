// 18-B公开展示专属播放／同帧／失败场景；不做新姿态推理或通用账号回归。
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {BrowserFixture,root} from './formal_browser_support.mjs';

const fixture=new BrowserFixture('ui-step18-showcase');const errors=[];const failureOnly=process.argv.includes('--failure-only');let alignment=null;
try{
  fixture.start();const context=await fixture.browser('Edge',{width:1440,height:1000});
  await context.addInitScript(()=>{window.showcaseCameraCalls=0;Object.defineProperty(navigator.mediaDevices,'getUserMedia',{value:async()=>{window.showcaseCameraCalls++;throw new Error('首页不得开启摄像头')}})});
  const out=path.join(root,'data/verification/step-18');
  if(!failureOnly){
  const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));await page.goto(fixture.origin+'/');
  await page.waitForFunction(()=>document.querySelector('#showcase-video').currentTime>.15&&document.querySelector('#showcase-canvas').dataset.frameSeq!==undefined);
  assert.equal(await page.evaluate(()=>showcaseCameraCalls),0);
  await page.evaluate(async()=>{const v=document.querySelector('#showcase-video');v.pause();await new Promise(resolve=>{v.addEventListener('seeked',resolve,{once:true});v.currentTime=20.8})});
  await page.locator('#showcase-count').filter({hasText:'2'}).waitFor();
  alignment=await page.evaluate(()=>{const v=document.querySelector('#showcase-video'),c=document.querySelector('#showcase-canvas');return {timeMs:Math.round(v.currentTime*1000),sourceMs:Number(c.dataset.sourceMs),width:v.videoWidth,height:v.videoHeight,paused:v.paused,mode:document.querySelector('.showcase-badges').textContent}});
  assert(alignment.paused&&alignment.sourceMs<=alignment.timeMs&&alignment.timeMs-alignment.sourceMs<40,JSON.stringify(alignment));assert(alignment.mode.includes('非实时画面'));
  await page.evaluate(()=>{const v=document.querySelector('#showcase-video');v.muted=false});assert.equal(await page.locator('#showcase-video').evaluate(v=>v.muted),false);
  await page.evaluate(()=>{document.querySelector('#showcase-video').muted=true});
  await page.evaluate(()=>{document.activeElement?.blur();window.scrollTo({top:0,behavior:'instant'})});await page.screenshot({path:path.join(out,'home-showcase.png'),fullPage:false});
  await page.locator('#showcase-fullscreen').click();await page.waitForFunction(()=>document.fullscreenElement?.id==='showcase-player');
  assert(await page.locator('#showcase-exit-fullscreen').isVisible());await page.screenshot({path:path.join(out,'home-showcase-fullscreen.png')});
  await page.locator('#showcase-exit-fullscreen').click();await page.waitForFunction(()=>!document.fullscreenElement);
  await page.locator('#showcase-play').click();await page.waitForFunction(()=>document.querySelector('#showcase-video').currentTime>21);
  await page.locator('#video-entry').scrollIntoViewIfNeeded();await page.waitForFunction(()=>document.querySelector('#showcase-video').paused);
  }
  const blocked=await context.newPage();blocked.on('pageerror',e=>errors.push(e.message));
  if(!failureOnly){
  await blocked.addInitScript(()=>{let first=true;const original=HTMLMediaElement.prototype.play;HTMLMediaElement.prototype.play=function(){if(first){first=false;return Promise.reject(new DOMException('fixture autoplay restriction','NotAllowedError'))}return original.call(this)}});
  await blocked.goto(fixture.origin+'/');await blocked.locator('#showcase-play').waitFor({state:'visible'});assert((await blocked.locator('#showcase-status').textContent()).includes('点击播放'));
  await blocked.locator('#showcase-play').click();await blocked.waitForFunction(()=>!document.querySelector('#showcase-video').paused);
  }
  await blocked.route('**/static/demo/barbell-row/track*',route=>route.abort());if(failureOnly)await blocked.goto(fixture.origin+'/');else await blocked.reload();await blocked.locator('#showcase-error').waitFor({state:'visible'});
  assert(await blocked.locator('#showcase-fullscreen').isDisabled());assert(!(await blocked.locator('#showcase-status').textContent()).includes('Failed to fetch'));
  assert(await blocked.locator('#showcase-player').isHidden());await blocked.evaluate(()=>window.scrollTo({top:0,behavior:'instant'}));await blocked.screenshot({path:path.join(out,'home-showcase-error.png')});
  await blocked.unroute('**/static/demo/barbell-row/track*');await blocked.locator('#showcase-retry').click();await blocked.waitForFunction(()=>!document.querySelector('#showcase-video').paused||!document.querySelector('#showcase-play').hidden);if(await blocked.locator('#showcase-play').isVisible())await blocked.locator('#showcase-play').click();
  await blocked.waitForFunction(()=>document.querySelector('#showcase-video').currentTime>.1);
  assert(await blocked.locator('#showcase-fullscreen').isEnabled());
  assert.equal(await blocked.evaluate(()=>showcaseCameraCalls),0);assert(await blocked.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));assert.deepEqual(errors,[]);
  const counts=JSON.parse(fixture.seed(`
import os,json
from sqlalchemy import select,func
from fitness_app.database import Database
from fitness_app import db_schema as s
db=Database(os.environ['FITNESS_DATABASE_PATH'])
with db.transaction(False) as c:print(json.dumps({'sessions':c.scalar(select(func.count()).select_from(s.analysis_sessions)),'jobs':c.scalar(select(func.count()).select_from(s.jobs))}))
db.dispose()
`).trim());assert.deepEqual(counts,{sessions:0,jobs:0});
  const result=failureOnly?{...JSON.parse(fs.readFileSync(path.join(out,'showcase-ui.json'),'utf8')),failureRecheck:'Chinese error, reserved fallback area and fullscreen disable/re-enable passed; other passed checks reused'}:{checks:'passed',scope:'public preprocessed video/skeleton showcase only',alignment,cameraCalls:0,...counts,autoplayBlockedRecovery:true,assetFailureRecovery:true,fullscreenWithSkeleton:true,actualAnalysisTested:false};
  fs.writeFileSync(path.join(out,'showcase-ui.json'),JSON.stringify(result,null,2)+'\n');console.log(JSON.stringify(result));
}finally{await fixture.close()}
