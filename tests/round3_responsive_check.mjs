// 覆盖尚未验过的页面/视口；只查布局，不重复模型和已通过的表单持久化。
import assert from 'node:assert/strict';
import path from 'node:path';
import {BrowserFixture,root} from './formal_browser_support.mjs';
const fixture=new BrowserFixture('round3-responsive'),errors=[];
const cases=[
  [320,740,['/video','/records','/records?view=list','/account','/account/ai','/account/training-profile','/account/sessions']],
  [430,932,['/realtime']],
  [768,1024,['/','/realtime','/video','/account/ai']],
  [1024,768,['/','/realtime','/account/ai']],
  [1920,1080,['/','/realtime']],
];
const activeCases=process.env.FITNESS_RESPONSIVE_SCOPE==='profile-shot'?[]:process.env.FITNESS_RESPONSIVE_SCOPE==='account'?[[320,740,['/account']]]:cases;
try{
  fixture.seed(`from fitness_app.paths import configure_environment
configure_environment()
import os
from fitness_app.database import Database
from fitness_app.auth import initialize_admin
db=Database(os.environ['FITNESS_DATABASE_PATH']);db.initialize();initialize_admin(db,'responsive_admin','Responsive-fixture-2026');db.dispose()`);
  fixture.start();const browser=await fixture.browser('Edge');await fixture.apiLogin(browser,'responsive_admin','Responsive-fixture-2026');
  await browser.addInitScript(()=>{window.cameraRequests=0;const original=window.matchMedia.bind(window);window.matchMedia=query=>query==='(pointer:coarse)'&&innerWidth<761?{matches:true,media:query,addEventListener(){},removeEventListener(){}}:original(query);if(navigator.mediaDevices)navigator.mediaDevices.getUserMedia=async()=>{window.cameraRequests++;throw new Error('no physical camera in responsive check');};});
  const page=await browser.newPage();page.on('pageerror',e=>errors.push(String(e)));
  const findings=[];
  for(const [width,height,urls]of activeCases){await page.setViewportSize({width,height});for(const url of urls){
    await page.goto(fixture.origin+url);await page.locator('main').waitFor();
    await page.evaluate(async()=>Promise.all(document.getAnimations().filter(a=>a.effect?.getTiming().iterations!==Infinity).map(a=>a.finished.catch(()=>{}))));
    const size=await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth,
      cameraRequests:window.cameraRequests,overflow:[...document.querySelectorAll('main *')].filter(x=>x.getBoundingClientRect().right>innerWidth+1&&getComputedStyle(x).position!=='fixed'&&!x.closest('.table-scroll')).slice(0,4).map(x=>x.className||x.id||x.tagName)}));
    if(size.scroll>size.width+1)findings.push({url,width,...size});assert.equal(size.cameraRequests,0);
  }}
  if(process.env.FITNESS_RESPONSIVE_SCOPE!=='account'){
  await page.setViewportSize({width:390,height:844});await page.goto(fixture.origin+'/account/training-profile');
  await page.locator('#training-profile-fields:not([disabled])').waitFor();
  await page.locator('#training-experience').selectOption('beginner');await page.locator('#training-minutes').fill('45');
  await page.locator('[name=goals][value=movement_quality]').check();await page.locator('[name=equipment][value=dumbbell]').check();
  await page.evaluate(async()=>{scrollTo({top:0,left:0,behavior:'instant'});await Promise.all(document.getAnimations().filter(a=>a.effect?.getTiming().iterations!==Infinity).map(a=>a.finished.catch(()=>{})));});
  await page.screenshot({path:path.join(root,'data/verification/round3/mobile-training-profile.png')});
  // 只检查新触屏默认入口，无相机API调用。
  if(process.env.FITNESS_RESPONSIVE_SCOPE!=='profile-shot'){
  await page.goto(fixture.origin+'/realtime');assert.equal(await page.locator('#live-camera-flip').innerText(),'切换前置相机');
  assert.equal(await page.locator('#live-mirror').isChecked(),false);
  }
  }
  assert.deepEqual(findings,[]);assert.deepEqual(errors,[]);console.log(activeCases.reduce((n,row)=>n+row[2].length,0)+' page/viewport combinations passed; no camera/model/API generation.');
}finally{await fixture.close();}
