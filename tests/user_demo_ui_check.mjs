// 只验证新演示播放、同源骨架和视频切换；不在浏览器重跑整段姿态分析。
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {BrowserFixture,root} from './formal_browser_support.mjs';

const fixture=new BrowserFixture('user-demo-ui');
const errors=[];
try{
  fixture.seed(`import os
from fitness_app.paths import configure_environment
configure_environment()
from fitness_app.database import Database
from fitness_app.auth import initialize_admin
db=Database(os.environ['FITNESS_DATABASE_PATH']);db.initialize()
initialize_admin(db,'demo_validator','Demo-validation-local-2026')
db.dispose()`);
  fixture.start();
  const browser=await fixture.browser('Edge',{width:1440,height:1180});
  await fixture.apiLogin(browser,'demo_validator','Demo-validation-local-2026');
  const page=await browser.newPage();page.on('pageerror',e=>errors.push(e.message));
  await page.goto(fixture.origin+'/');
  await page.waitForFunction(()=>document.querySelector('#showcase-video').currentTime>.2&&document.querySelector('#showcase-canvas').dataset.sourceMs);
  assert((await page.locator('.showcase-badges').innerText()).includes('蝴蝶机夹胸'));
  assert((await page.locator('#showcase-video').getAttribute('src')).includes('user-1575'));
  await page.locator('#showcase-toggle').click();
  await page.evaluate(async()=>{const v=document.querySelector('#showcase-video');await new Promise(resolve=>{v.addEventListener('seeked',resolve,{once:true});v.currentTime=10;});});
  const aligned=await page.evaluate(()=>{const v=document.querySelector('#showcase-video'),c=document.querySelector('#showcase-canvas');return {width:v.videoWidth,height:v.videoHeight,media:Number(c.dataset.mediaMs),source:Number(c.dataset.sourceMs),paused:v.paused};});
  assert.equal(aligned.width,1280);assert.equal(aligned.height,720);
  assert(aligned.paused&&aligned.media>=aligned.source&&aligned.media-aligned.source<=100,JSON.stringify(aligned));
  assert.equal(await page.locator('#showcase-feedback').getAttribute('data-state'),'clear');
  assert((await page.locator('#showcase-feedback').innerText()).includes('动作正常'));
  assert.equal(await page.locator('[data-feedback-instruction]').isVisible(),false);
  assert.equal(await page.locator('#showcase-exit-fullscreen').isVisible(),false);
  assert(await page.evaluate(()=>{const intro=document.querySelector('.hero-intro').getBoundingClientRect(),video=document.querySelector('#showcase-player').getBoundingClientRect();return intro.bottom<=video.top&&Math.abs(video.x+video.width/2-innerWidth/2)<2;}),'首页标题在上、视频水平居中');
  await page.evaluate(()=>scrollTo({top:0,behavior:'instant'}));
  await page.screenshot({path:path.join(root,'data/verification/round3/home-user-demo.png')});
  await page.setViewportSize({width:390,height:844});
  await page.evaluate(()=>scrollTo({top:0,behavior:'instant'}));
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await page.screenshot({path:path.join(root,'data/verification/round3/home-user-demo-mobile.png')});
  await page.setViewportSize({width:1440,height:1180});
  await page.locator('#showcase-fullscreen').click();
  await page.waitForFunction(()=>document.fullscreenElement?.id==='showcase-player');
  await page.locator('#showcase-player #showcase-feedback').waitFor({state:'visible'});
  assert(await page.locator('#showcase-player #showcase-feedback').isVisible());
  await page.locator('#showcase-exit-fullscreen').click();
  await page.waitForFunction(()=>!document.fullscreenElement);
  assert.equal(await page.locator('#showcase-exit-fullscreen').isVisible(),false);
  await page.locator('#showcase-player #showcase-feedback').waitFor({state:'visible'});
  await page.goto(fixture.origin+'/video?demo=1');
  await page.waitForFunction(()=>document.querySelector('#video-start').disabled===false);
  assert.equal(await page.locator('#video-demo-select').count(),1);
  assert.equal(await page.locator('#video-action').inputValue(),'pec_deck_fly');
  assert.equal(await page.locator('#video-choices').isVisible(),false);
  assert((await page.locator('#video-player').getAttribute('src')).includes('user-1575'));
  assert(!(await page.locator('#video-file-name').innerText()).includes('1575'));
  await page.reload();
  await page.waitForFunction(()=>!document.querySelector('#video-start').disabled);
  assert.equal(await page.locator('#video-demo-credit summary').count(),0);
  // 两段展示演示按编号选择，归档原片继续可访问。
  await page.goto(fixture.origin+'/video?demo=1&demo_id=user-1694');
  await page.waitForFunction(()=>!document.querySelector('#video-start').disabled);
  assert.equal(await page.locator('#video-action').inputValue(),'machine_chest_press');
  assert.equal(await page.locator('#video-demo-select option').count(),2);
  await page.locator('#video-demo-select').selectOption('user-1575');
  await page.waitForURL(fixture.origin+'/video?demo=1&demo_id=user-1575');
  await page.locator('#video-start:not([disabled])').waitFor();
  assert((await page.locator('#video-player').getAttribute('src')).includes('user-1575'));
  await page.reload();await page.locator('#video-start:not([disabled])').waitFor();
  assert.equal(await page.locator('#video-demo-select').inputValue(),'user-1575');
  for(const width of [320,375,414,768,1440]){
    await page.setViewportSize({width,height:1000});
    for(const route of ['/','/video','/realtime','/records','/account']){
      await page.goto(fixture.origin+route);
      assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),route+' overflow '+width);
      assert(await page.locator('.topbar .brand').isVisible());
      assert.equal(await page.locator('.topbar nav a').count(),4);
    }
  }
  // 实际链接点击、下拉菜单、筛选与按压反馈；不启动相机或付费服务。
  await page.setViewportSize({width:1440,height:1000});
  await page.goto(fixture.origin+'/');
  for(const route of ['/realtime','/video','/records','/']){
    await page.locator('.topbar nav a[href="'+route+'"]').click();
    await page.waitForURL(fixture.origin+route);
    assert(await page.locator('main').isVisible());
  }
  const intervals=await page.evaluate(async()=>{const times=[];let last;const menu=document.querySelector('#account-menu');for(let i=0;i<90;i++){const now=await new Promise(requestAnimationFrame);if(last!==undefined)times.push(now-last);last=now;if(i%15===0)menu.open=!menu.open;}menu.open=false;return times.sort((a,b)=>a-b);});
  const p95=intervals[Math.floor(intervals.length*.95)];assert(p95<60,'本机界面帧间隔异常');
  console.log('Menu animation p95 frame interval: '+p95.toFixed(2)+' ms (local headless browser)');
  await page.locator('#account-menu summary').click();
  await page.locator('.account-dropdown a[href="/account"]').click();
  await page.waitForURL(fixture.origin+'/account');
  await page.screenshot({path:path.join(root,'前端重设计/实现核对/个人中心.png'),animations:'disabled'});
  for(const [route,name] of [['/video','视频选择'],['/realtime','实时准备'],['/records','训练概览']]){
    await page.goto(fixture.origin+route);
    if(route==='/records')await page.locator('#overview-loading').waitFor({state:'hidden'});
    await page.screenshot({path:path.join(root,'前端重设计/实现核对/'+name+'.png'),animations:'disabled'});
  }
  await page.goto(fixture.origin+'/');
  assert.equal(await page.locator('.movement-section').count(),0);
  const upload=page.locator('.hero-intro .button[href="/video"]');
  await upload.dispatchEvent('pointerdown');
  assert((await upload.getAttribute('class')).includes('is-pressed'));
  await page.mouse.up();
  await page.emulateMedia({reducedMotion:'reduce'});
  await page.goto(fixture.origin+'/');
  await page.waitForFunction(()=>document.querySelector('#showcase-fullscreen').disabled===false);
  assert(await page.locator('#showcase-video').evaluate(v=>v.paused));
  await page.locator('#showcase-toggle').click();
  await page.waitForFunction(()=>!document.querySelector('#showcase-video').paused);
  await page.emulateMedia({reducedMotion:'no-preference'});
  await page.route('**/static/demo/user-1575/track*',route=>route.abort());
  await page.goto(fixture.origin+'/');
  await page.locator('#showcase-error').waitFor({state:'visible'});
  assert(await page.locator('#showcase-player').isHidden());
  await page.unroute('**/static/demo/user-1575/track*');
  await page.locator('#showcase-retry').click();
  await page.waitForFunction(()=>document.querySelector('#showcase-video').currentTime>.2);
  // 正常视频证据和截图在上方已验；下面只用明确工程夹具验证错误/未知分支，不保存虚构错误截图。
  const actual=JSON.parse(fs.readFileSync(path.join(root,'fitness_app/static/demo/user-1575-track.json'),'utf8'));
  const issue=JSON.parse(fixture.seed(`import sys,json
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/'tests'))
from test_demo_feedback import result
from fitness_app.demo_feedback import build_demo_feedback
print(json.dumps(build_demo_feedback(*result(True)),ensure_ascii=False))`).trim());
  issue.title='工程测试：'+issue.title;
  const mocked=structuredClone(actual);
  for(const frame of mocked.frames)frame.feedback=issue;
  await page.route('**/static/demo/user-1575/track*',route=>route.fulfill({json:mocked}));
  await page.goto(fixture.origin+'/');
  await page.waitForFunction(()=>document.querySelector('#showcase-feedback').dataset.state==='issue');
  assert((await page.locator('#showcase-feedback').innerText()).includes('手臂'));
  assert((await page.locator('[data-feedback-instruction]').innerText()).includes('放慢'));
  assert(await page.locator('[data-feedback-instruction]').isVisible());
  await page.evaluate(async()=>{const v=document.querySelector('#showcase-video');v.pause();await new Promise(resolve=>{v.addEventListener('seeked',resolve,{once:true});v.currentTime=10;});});
  const red=await page.locator('#showcase-canvas').evaluate(canvas=>{const pixels=canvas.getContext('2d').getImageData(0,0,canvas.width,canvas.height).data;let red=0;for(let i=0;i<pixels.length;i+=4)if(pixels[i]>160&&pixels[i+1]<130&&pixels[i+2]<140&&pixels[i+3]>100)red++;return red;});
  assert(red>0,'错误分支应实际绘制红色问题部位');
  await page.unroute('**/static/demo/user-1575/track*');
  const uncertain=structuredClone(actual);
  for(const frame of uncertain.frames){frame.feedback={...issue,state:'unassessable',title:'工程测试：暂时无法判断',description:'关键部位不可见',instruction:'调整取景后再判断',highlight:null,checks:issue.checks.map(item=>({...item,status:'unassessable',detail:'暂不能判断'}))};}
  await page.route('**/static/demo/user-1575/track*',route=>route.fulfill({json:uncertain}));
  await page.reload();
  await page.waitForFunction(()=>document.querySelector('#showcase-feedback').dataset.state==='unassessable');
  assert(!(await page.locator('[data-feedback-title]').innerText()).includes('正常'));
  const unknownRed=await page.locator('#showcase-canvas').evaluate(canvas=>{const pixels=canvas.getContext('2d').getImageData(0,0,canvas.width,canvas.height).data;let red=0;for(let i=0;i<pixels.length;i+=4)if(pixels[i]>160&&pixels[i+1]<130&&pixels[i+2]<140&&pixels[i+3]>100)red++;return red;});
  assert.equal(unknownRed,0);
  await page.unroute('**/static/demo/user-1575/track*');
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({home:'user-1575',dimensions:aligned,choices:1,legacyLinkUsesDefault:true,mobileOverflow:false,trackFailureRecovery:true,realClearFeedback:true,fullscreenFeedback:true,syntheticIssueAndUnknownBranches:true,pageErrors:errors}));
}finally{await fixture.close();}
