// 首页两段真实素材轮播及新增横屏演示选择；不启动分析或付费AI。
import assert from 'node:assert/strict';
import path from 'node:path';
import {BrowserFixture,root} from './formal_browser_support.mjs';
const fx=new BrowserFixture('home-demo-playlist');const errors=[];
try{
 fx.seed(`import os
from fitness_app import create_app
from fitness_app.auth import initialize_admin
app=create_app({'TESTING':True,'DATABASE_PATH':os.environ['FITNESS_DATABASE_PATH']})
initialize_admin(app.extensions['database'],'Playlist_Admin','local-playlist-fixture-password')
app.extensions['database'].dispose()`);
 fx.start();const context=await fx.browser('Edge',{width:1440,height:1050});
 const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
 await page.goto(fx.origin+'/');
 await page.waitForFunction(()=>document.querySelector('#home-demo').dataset.demoId==='user-1694'&&document.querySelector('#showcase-video').currentTime>.2);
 assert.deepEqual(await page.locator('#home-demo-playlist').evaluate(n=>JSON.parse(n.textContent).map(x=>x.id)),['user-1694','user-1689']);
 assert.equal(await page.locator('.showcase-badges strong').innerText(),'器械推胸');
 assert(!/内置演示|演示视频/.test(await page.locator('#home-demo').innerText()));
 const seek=async seconds=>{await page.locator('#showcase-video').evaluate((v,t)=>{v.pause();v.currentTime=t;},seconds);
  await page.waitForFunction(t=>Math.abs(Number(document.querySelector('#showcase-canvas').dataset.mediaMs)-t*1000)<100,seconds);};
 const checkFeedback=async(seconds,state,title)=>{await seek(seconds);
  assert.equal(await page.locator('#showcase-feedback').getAttribute('data-state'),state);
  assert.equal(await page.locator('[data-feedback-title]').innerText(),title);};
 assert(Math.abs(await page.locator('#showcase-video').evaluate(v=>v.duration)-21.067)<.04);
 await checkFeedback(.5,'observing','正在分析');
 assert(await page.locator('[data-feedback-instruction]').isHidden());
 await page.locator('#showcase-player').screenshot({path:path.join(root,'第四轮优化/实现核对/首页-正在分析.png'),animations:'disabled'});
 await checkFeedback(3,'issue','身体侧倾、握持不当');
 assert(await page.locator('[data-feedback-instruction]').isVisible());
 await checkFeedback(.2,'observing','正在分析'); // 回拖后不能残留上一时刻的问题。
 await checkFeedback(3,'issue','身体侧倾、握持不当');
 const finish=async()=>page.locator('#showcase-video').evaluate(async v=>{v.currentTime=v.duration-.12;await v.play();});
 await finish();
 await page.waitForFunction(()=>document.querySelector('#home-demo').dataset.demoId==='user-1689'&&document.querySelector('#showcase-video').readyState>=2);
 assert.equal(await page.locator('.showcase-badges strong').innerText(),'高位下拉');
 assert.equal(await page.locator('#showcase-video').evaluate(v=>v.videoWidth/v.videoHeight),16/9);
 await checkFeedback(.5,'observing','正在分析');
 await seek(4);
 assert(await page.locator('#showcase-video').evaluate(v=>v.paused));
 await page.locator('#showcase-player').screenshot({path:path.join(root,'第四轮优化/实现核对/高位下拉-横屏演示.png'),animations:'disabled'});
 await page.locator('#showcase-toggle').click();await finish();
 await page.waitForFunction(()=>document.querySelector('#home-demo').dataset.demoId==='user-1694'&&document.querySelector('#showcase-video').readyState>=2);
 await page.locator('#showcase-toggle:not([disabled])').click();
 await page.screenshot({path:path.join(root,'第四轮优化/实现核对/首页.png'),fullPage:true,animations:'disabled'});
 await page.setViewportSize({width:390,height:844});await page.goto(fx.origin+'/');
 await page.waitForFunction(()=>document.querySelector('#showcase-video').readyState>=2);
 await checkFeedback(.5,'observing','正在分析');await checkFeedback(3,'issue','身体侧倾、握持不当');
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await page.locator('#showcase-player').screenshot({path:path.join(root,'data/verification/round3/demo-annotation-mobile.png'),animations:'disabled'});
 await page.setViewportSize({width:1440,height:1050});
 await fx.apiLogin(context,'Playlist_Admin','local-playlist-fixture-password');
 for(const [id,title] of [['user-1688','高位下拉（错误演示）'],['user-1689','高位下拉（正确演示）']]){
  await page.goto(fx.origin+'/video?demo=1&demo_id='+id);await page.locator('#video-start:not([disabled])').waitFor();
  assert.equal(await page.locator('#video-action').inputValue(),'lat_pulldown');
  assert.equal(await page.locator('#video-demo-select option:checked').innerText(),title);
  await page.waitForFunction(()=>document.querySelector('#video-player').readyState>=2);
  assert.deepEqual(await page.locator('#video-player').evaluate(v=>[v.videoWidth,v.videoHeight]),[1280,720]);
 }
 await page.goto(fx.origin+'/video');assert.equal(await page.locator('.video-demo-card:visible').count(),4);
 await page.screenshot({path:path.join(root,'第四轮优化/实现核对/视频选择.png'),fullPage:true,animations:'disabled'});
 assert.deepEqual(errors,[]);console.log(JSON.stringify({carousel:['user-1694','user-1689','user-1694'],cleanHomeLabels:true,landscapePairSelectable:true,realBrowserDecode:true,pageErrors:errors}));
}finally{await fx.close();}
