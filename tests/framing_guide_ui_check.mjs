// 只检查新增取景控件与机位来源，不请求真实相机，不重复旧模型或账号矩阵。
import assert from 'node:assert/strict';
import path from 'node:path';
import {BrowserFixture,root} from './formal_browser_support.mjs';
const fixture=new BrowserFixture('round3-framing-ui'),errors=[];
try{
 fixture.seed("from fitness_app.paths import configure_environment;configure_environment();import os;from fitness_app.database import Database;from fitness_app.auth import initialize_admin;db=Database(os.environ['FITNESS_DATABASE_PATH']);db.initialize();initialize_admin(db,'Framing_Admin','Framing-fixture-2026');db.dispose()");
 fixture.start();const browser=await fixture.browser('Edge',{width:390,height:844});await fixture.apiLogin(browser,'Framing_Admin','Framing-fixture-2026');
 await browser.addInitScript(()=>{window.cameraRequests=0;if(navigator.mediaDevices)navigator.mediaDevices.getUserMedia=async()=>{window.cameraRequests++;throw new Error('No physical camera in this UI check');};});
 const page=await browser.newPage();page.on('pageerror',e=>errors.push(String(e)));
 await page.goto(fixture.origin+'/realtime');await page.locator('#live-settings summary').click();
 await page.locator('#live-view').selectOption('front');await page.locator('#live-toggle:not([disabled])').waitFor();
 assert.equal(await page.evaluate(()=>window.cameraRequests),0);assert.equal(await page.locator('#live-view').inputValue(),'front');

 await page.evaluate(()=>document.querySelector('.live-foot').scrollIntoView({block:'start',behavior:'instant'}));
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await page.screenshot({path:path.join(root,'第五轮优化/实现核对/取景控件-手机.png')});
 await page.setViewportSize({width:320,height:740});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await page.goto(fixture.origin+'/video');await page.locator('#video-select').waitFor();assert(await page.locator('#video-drop').isVisible());assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 assert.deepEqual(errors,[]);console.log('Realtime framing selection and video entry: auto action preserved, 320/390 layout, no camera activation: passed.');
}finally{await fixture.close();}
