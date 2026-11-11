// 本轮控件外观与表单反馈检查：不运行模型、不访问物理摄像头。
import path from 'node:path';
import assert from 'node:assert/strict';
import {BrowserFixture,root} from './formal_browser_support.mjs';
const fx=new BrowserFixture('ui-step21-controls'),output=path.join(root,'前端重设计/实现核对');
try {
  fx.seed(`import os
from fitness_app import create_app
from fitness_app.auth import initialize_admin
app=create_app({'TESTING':True,'DATABASE_PATH':os.environ['FITNESS_DATABASE_PATH']})
initialize_admin(app.extensions['database'],'Controls_UI','ui-controls-test-password')
app.extensions['database'].dispose()
`);
  fx.start();
  const context=await fx.browser('Edge',{width:1536,height:1024});
  const page=await context.newPage();page.setDefaultTimeout(15000);
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  const profileOnly=process.argv.includes('--profile-only');
  const shot=async name=>{await page.evaluate(()=>scrollTo(0,0));await page.evaluate(()=>document.fonts.ready);await page.waitForTimeout(180);await page.screenshot({path:path.join(output,name+'.png'),fullPage:true,animations:'disabled'});};
  if(!profileOnly){await page.goto(fx.origin+'/login');await page.locator('.auth-visual img').evaluate(i=>i.decode());
  assert(await page.locator('.primary-submit').isEnabled());
  await page.waitForFunction(()=>getComputedStyle(document.querySelector('.primary-submit')).backgroundColor==='rgb(61, 68, 78)');
  const primary=await page.locator('.primary-submit').evaluate(n=>{const s=getComputedStyle(n);return {height:n.getBoundingClientRect().height,color:s.color,bg:s.backgroundColor,radius:s.borderRadius};});
  assert.equal(primary.height,60);assert.equal(primary.bg,'rgb(61, 68, 78)');assert.equal(primary.radius,'8px');
  await shot('登录');
  let submissions=0;page.on('request',r=>{if(r.url().endsWith('/api/v1/auth/login')&&r.method()==='POST')submissions++;});
  await page.locator('.primary-submit').click();
  assert(await page.locator('#email-error').isVisible());assert.equal(submissions,0);
  assert.equal(await page.evaluate(()=>document.activeElement.id),'username');
  await page.locator('#username').fill('Controls_UI');await page.locator('#password').fill('ui-controls-test-password');
  await page.locator('.primary-submit').click();await page.waitForURL(fx.origin+'/');assert.equal(submissions,1);
  }else{await fx.apiLogin(context,'Controls_UI','ui-controls-test-password');}
  await page.goto(fx.origin+'/account');await page.waitForFunction(()=>!document.querySelector('#nickname').disabled);
  await page.locator('#nickname').fill('林一同学');assert(await page.locator('#profile-save').isEnabled());assert.equal(Math.round((await page.locator('#profile-save').boundingBox()).width),164);await page.locator('h1').click();await shot('个人中心');await page.locator('#profile-cancel').click();
  await page.goto(fx.origin+'/realtime');await shot('实时准备');
  if(!profileOnly){
  const toggle=page.locator('#live-skeleton');assert(await toggle.isChecked());await toggle.uncheck();assert(!(await toggle.isChecked()));await page.keyboard.press('Tab');
  await page.mouse.move(0,0);await page.waitForFunction(()=>getComputedStyle(document.querySelector('.live-view-tools label')).backgroundColor==='rgba(0, 0, 0, 0)');
  assert.equal(await page.locator('.live-view-tools label').first().evaluate(n=>getComputedStyle(n).backgroundColor),'rgba(0, 0, 0, 0)');
  await toggle.check();await page.locator('.live-toolbar').screenshot({path:path.join(output,'实时工具栏.png'),animations:'disabled'});
  const toolbar=await page.locator('.live-toolbar').boundingBox();assert(Math.abs(toolbar.x+toolbar.width/2-768)<2);
  await page.setViewportSize({width:1280,height:800});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));await shot('实时准备-窄屏');
  await page.emulateMedia({reducedMotion:'reduce'});assert.equal(await page.locator('#live-toggle').evaluate(n=>getComputedStyle(n).transitionDuration),'0s');
  }else{await page.locator('.live-toolbar').screenshot({path:path.join(output,'实时工具栏.png'),animations:'disabled'});}
  assert.deepEqual(errors,[]);
  console.log(profileOnly?'PASS: changed profile button geometry and updated visual evidence.':'PASS: control geometry, enabled form validation, one actual login, styled checkbox hit areas, viewport and reduced motion.');
} finally { await fx.close(); }
