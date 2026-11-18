// 首页两段真实素材轮播及新增横屏演示选择；不启动分析或付费AI。
import assert from 'node:assert/strict';
import path from 'node:path';
import {BrowserFixture,root} from './formal_browser_support.mjs';
const output=path.join(root,'第五轮优化/实现核对');const fx=new BrowserFixture('home-demo-playlist');const errors=[],readTimings=[],analysisTimings=[];
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
 await page.locator('#showcase-player').screenshot({path:path.join(output,'首页提示-首页-正在分析.png'),animations:'disabled'});
 await checkFeedback(3,'issue','身体侧倾、握持不当');
 assert(await page.locator('[data-feedback-instruction]').isVisible());
 await checkFeedback(.2,'observing','正在分析'); // 回拖后不能残留上一时刻的问题。
 await checkFeedback(3,'issue','身体侧倾、握持不当');
 for(const width of [1440,1024,768,390,320]){
  await page.setViewportSize({width,height:1050});
  const layout=await page.locator('#showcase-feedback').evaluate(panel=>({title:parseFloat(getComputedStyle(panel.querySelector('[data-feedback-title]')).fontSize),text:parseFloat(getComputedStyle(panel.querySelector('[data-feedback-instruction]')).fontSize),top:panel.getBoundingClientRect().top,mediaTop:document.querySelector('#showcase-video').getBoundingClientRect().top,mediaBottom:document.querySelector('#showcase-video').getBoundingClientRect().bottom,panelBottom:panel.getBoundingClientRect().bottom,toolsTop:document.querySelector('.showcase-tools').getBoundingClientRect().top,label:panel.querySelector('[data-feedback-label]').textContent,nextHidden:panel.querySelector('[data-feedback-next]').hidden}));
  assert(layout.title>=(width<=767?19:25));assert(layout.text>=(width<=767?15:18));assert(layout.top>=layout.mediaTop&&layout.panelBottom<=layout.mediaBottom,'纠正提示按用户要求显示在视频内 '+JSON.stringify({width,...layout}));if(width<=767)assert(layout.panelBottom<=layout.toolsTop,'手机提示与播放控件分开');assert.equal(layout.label,'动作纠正提示');assert.equal(layout.nextHidden,false);assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  if(width===1440||width===390)await page.locator('#showcase-player').screenshot({path:path.join(output,'首页纠正提示-'+width+'.png'),animations:'disabled'});
 }
 await page.setViewportSize({width:1440,height:1050});
 await page.locator('#showcase-fullscreen').click();await page.waitForFunction(()=>document.fullscreenElement?.id==='showcase-player');assert(await page.locator('#showcase-exit-fullscreen').isVisible());await page.locator('#showcase-exit-fullscreen').click();await page.waitForFunction(()=>!document.fullscreenElement);
 await checkFeedback(.2,'observing','正在分析');assert(await page.locator('[data-feedback-next]').isHidden());await checkFeedback(3,'issue','身体侧倾、握持不当');
 const finish=async()=>page.locator('#showcase-video').evaluate(async v=>{v.currentTime=v.duration-.12;await v.play();});
 await finish();
 await page.waitForFunction(()=>document.querySelector('#home-demo').dataset.demoId==='user-1689'&&document.querySelector('#showcase-video').readyState>=2);
 assert.equal(await page.locator('.showcase-badges strong').innerText(),'高位下拉');
 await page.waitForFunction(()=>document.querySelector('#showcase-video').videoWidth===1280&&document.querySelector('#showcase-video').videoHeight===720);assert.equal(await page.locator('#showcase-video').evaluate(v=>v.videoWidth/v.videoHeight),16/9);
 await checkFeedback(.5,'observing','正在分析');
 await seek(4);
 assert(await page.locator('#showcase-video').evaluate(v=>v.paused));
 await page.locator('#showcase-player').screenshot({path:path.join(output,'首页提示-高位下拉-横屏演示.png'),animations:'disabled'});
 await page.locator('#showcase-toggle').click();await finish();
 await page.waitForFunction(()=>document.querySelector('#home-demo').dataset.demoId==='user-1694'&&document.querySelector('#showcase-video').readyState>=2);
 await page.locator('#showcase-toggle:not([disabled])').click();
 await page.screenshot({path:path.join(output,'首页提示-首页.png'),fullPage:true,animations:'disabled'});
 await page.setViewportSize({width:390,height:844});await page.goto(fx.origin+'/');
 await page.waitForFunction(()=>document.querySelector('#showcase-video').readyState>=2);
 await checkFeedback(.5,'observing','正在分析');await checkFeedback(3,'issue','身体侧倾、握持不当');
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await page.locator('#showcase-player').screenshot({path:path.join(output,'首页提示-手机.png'),animations:'disabled'});
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
 const writes=[];page.on('request',r=>{if(r.method()==='POST')writes.push(new URL(r.url()).pathname);});
 for(const id of ['user-1575','user-1694','user-1688','user-1689']){const began=Date.now();const response=await page.goto(fx.origin+'/video/examples/'+id);assert.equal(response.status(),200,(await response.text()).slice(0,2000));await page.locator('.saved-example-feedback').waitFor();await page.waitForFunction(()=>document.querySelector('#showcase-video').readyState>=2);assert((await page.locator('.page-heading').innerText()).includes('分析完成'));assert(await page.getByRole('region',{name:'本次分析结论'}).isVisible());readTimings.push({demo_id:id,ready_ms:Date.now()-began});assert(Date.now()-began<10000);assert(await page.locator('#showcase-video').evaluate(v=>v.paused));if(await page.locator('[data-review-seek]').count()){const target=Number(await page.locator('[data-review-seek]').first().getAttribute('data-review-seek'));await page.waitForFunction(t=>Math.abs(Number(document.querySelector('#showcase-canvas').dataset.mediaMs)-t)<100,target);assert.equal(await page.locator('#showcase-feedback').getAttribute('data-origin'),'confirmed_review');}if(await page.locator('[data-example-seek]').count()){const target=Number(await page.locator('[data-example-seek]').first().getAttribute('data-example-seek'));await page.locator('[data-example-seek]').first().click();await page.waitForFunction(t=>Math.abs(Number(document.querySelector('#showcase-canvas').dataset.mediaMs)-t)<100,target);assert.equal(await page.locator('#showcase-feedback').getAttribute('data-state'),'issue');}await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.setViewportSize({width:1440,height:1050});}
 assert.equal((await context.request.get(fx.origin+'/video/examples/not-published')).status(),404);assert.equal((await context.request.get(fx.origin+'/static/demo/not-published/analysis-track')).status(),404);
 for(const id of ['user-1575','user-1694','user-1688','user-1689']){
  await page.goto(fx.origin+'/video');const card=page.locator('.video-demo-card[href="/video?demo=1&demo_id='+id+'"]:visible');await card.click();await page.waitForURL('**/video?demo=1&demo_id='+id);await page.locator('#video-start:not([disabled])').waitFor();assert.equal(await page.locator('.saved-example-feedback').count(),0);assert((await page.locator('#video-input-state').innerText()).includes('点击开始分析'));
  let release;const gate=new Promise(resolve=>{release=resolve;});const pattern='**/static/demo/'+id+'/analysis-track*';
  await page.route(pattern,async route=>{await gate;await route.continue();});
  await page.locator('#video-start').click();await page.locator('#video-progress-panel').waitFor();assert.equal(await page.locator('#video-page').getAttribute('data-stage'),'processing');assert((await page.locator('#video-state').innerText()).includes('准备视频分析'));assert.equal(await page.locator('.saved-example-summary').count(),0);
  if(id==='user-1688')await page.screenshot({path:path.join(output,'内置视频-分析处理中.png'),animations:'disabled'});
  const duration=await page.locator('#video-player').evaluate(v=>v.duration),began=Date.now();
  release();await page.waitForURL('**/video/examples/'+id);const elapsed=Date.now()-began;analysisTimings.push({demo_id:id,duration_ms:duration*1000,elapsed_ms:elapsed});assert(elapsed>=duration/4*800,'复核按实际原片播放推进，不瞬间完成');await page.unroute(pattern);await page.locator('.saved-example-feedback').waitFor();assert((await page.locator('.page-heading').innerText()).includes('分析完成'));assert(await page.getByRole('region',{name:'本次分析结论'}).isVisible());
 }
 assert.deepEqual(writes,[],'内置视频实际核对同源姿态数据，不上传或重复启动GPU任务');
 await page.goto(fx.origin+'/video?demo=1&demo_id=user-1688');await page.locator('#video-start:not([disabled])').waitFor();
 const failedTrack='**/static/demo/user-1688/analysis-track*';
 await page.route(failedTrack,()=>{});await page.locator('#video-start').click();await page.locator('#video-progress-panel').waitFor();await page.locator('#video-cancel').click();await page.locator('#video-start:not([disabled])').waitFor();assert(await page.locator('#video-error').isHidden());assert.equal(await page.locator('.saved-example-summary').count(),0);await page.unroute(failedTrack);
 await page.locator('#video-start').click();await page.locator('#video-state').filter({hasText:'正在分析动作片段'}).waitFor();assert.equal(await page.locator('#video-player').evaluate(v=>v.playbackRate),4);assert(!/复核|倍速/.test(await page.locator('#video-page').innerText()));await page.screenshot({path:path.join(output,'内置视频-分析处理中.png'),animations:'disabled'});await page.locator('#video-cancel').click();await page.locator('#video-start:not([disabled])').waitFor();assert(await page.locator('#video-player').evaluate(v=>v.paused&&v.playbackRate===1));assert(await page.locator('#video-live-skeleton').isHidden());
 await page.route(failedTrack,async route=>{const response=await route.fetch(),track=await response.json();await route.fulfill({response,json:{...track,source_sha256:'0'.repeat(64)}});});
 await page.locator('#video-start').click();await page.locator('#video-error').filter({hasText:'不匹配'}).waitFor();assert(await page.locator('#video-start').isEnabled());assert.equal(await page.locator('.saved-example-summary').count(),0);await page.unroute(failedTrack);
 await page.route(failedTrack,route=>route.fulfill({status:503,body:'temporarily unavailable'}));
 await page.locator('#video-start').click();await page.locator('#video-error').filter({hasText:'读取失败'}).waitFor();assert(await page.locator('#video-start').isEnabled());assert.equal(await page.locator('.saved-example-summary').count(),0);await page.unroute(failedTrack);
 await page.locator('#video-start').click();await page.waitForURL('**/video/examples/user-1688');await page.locator('.saved-example-summary[data-state="issue"]').waitFor();await page.waitForFunction(()=>document.querySelector('#showcase-feedback').dataset.origin==='confirmed_review');assert((await page.locator('.saved-example-review').innerText()).includes('00:02.0–00:15.4'));assert((await page.locator('[data-example-seek]').first().locator('..').innerText()).includes('00:05.8–00:06.2'));await page.screenshot({path:path.join(output,'内置视频-分析结论.png'),animations:'disabled'});
 const reanalyse=page.getByRole('link',{name:'重新分析',exact:true});assert((await reanalyse.getAttribute('href')).includes('fresh=1'));await reanalyse.click();await page.locator('#video-start:not([disabled])').waitFor();assert((await page.locator('#video-input-state').innerText()).includes('视频校验通过'));assert(writes.includes('/api/v1/demo-inputs'),'明确重新分析仍准备真实视频输入');await page.goto(fx.origin+'/video?demo=1&demo_id=user-1688');await page.locator('#video-start:not([disabled])').waitFor();await page.screenshot({path:path.join(output,'内置视频-开始分析.png'),animations:'disabled'});
await page.screenshot({path:path.join(output,'内置视频-快速回看.png'),animations:'disabled'});
 await page.screenshot({path:path.join(output,'首页提示-视频选择.png'),fullPage:true,animations:'disabled'});
 assert.deepEqual(errors,[]);console.log(JSON.stringify({carousel:['user-1694','user-1689','user-1694'],cleanHomeLabels:true,landscapePairSelectable:true,realBrowserDecode:true,savedResultReadMs:readTimings,analysisTimings,videoDrivenFourTimesReview:true,preparedAnalysisProgressAndConclusion:true,cancelBeforeResult:true,cancelDuringPlayback:true,separateReviewAndEvidenceRanges:true,mismatchedSourceRejected:true,readFailureRetried:true,duplicateGpuJobs:0,freshAnalysisPreparesRealInput:true,pageErrors:errors}));
}finally{await fx.close();}
