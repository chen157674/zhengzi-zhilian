// 共享导航、菜单归并、复盘空状态及多尺寸布局；仅使用隔离测试库。
import assert from 'node:assert/strict';
import path from 'node:path';
import {BrowserFixture,root} from './formal_browser_support.mjs';

const fx=new BrowserFixture('frontend-polish'),errors=[];
const out=path.join(root,'前端重设计/实现核对');
try{
 fx.seed(`import os
from fitness_app.paths import configure_environment
configure_environment()
from fitness_app import create_app
from fitness_app.auth import initialize_admin
app=create_app({'TESTING':True,'DATABASE_PATH':os.environ['FITNESS_DATABASE_PATH']})
initialize_admin(app.extensions['database'],'Polish_Admin','polish-test-password')
app.extensions['database'].dispose()`);
 fx.start();const context=await fx.browser('Edge',{width:1440,height:1000});
 await fx.apiLogin(context,'Polish_Admin','polish-test-password');
 await context.addInitScript(()=>{window.cameraRequests=0;if(navigator.mediaDevices)navigator.mediaDevices.getUserMedia=async()=>{window.cameraRequests++;throw new Error('No camera in visual check');};});
 const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
 const shot=async name=>{await page.evaluate(()=>document.fonts.ready);await page.screenshot({path:path.join(out,name+'.png'),animations:'disabled'});};
 let header;
 for(const route of ['/','/video','/realtime','/records','/records?view=list','/account','/settings','/account/ai','/account/sessions','/account/password']){
  await page.goto(fx.origin+route);
  const sizes=await page.locator('.topbar').evaluate(node=>({height:node.getBoundingClientRect().height,links:[...node.querySelectorAll('nav a')].map(a=>({width:a.getBoundingClientRect().width,height:a.getBoundingClientRect().height,font:getComputedStyle(a).fontSize}))}));
  if(!header)header=sizes;else assert.deepEqual(sizes,header,'各页面导航尺寸应相同：'+route);
  assert.equal(sizes.links[0].font,'16px');
  assert(sizes.links.every(link=>link.height>=44));
  if(route==='/video')await shot('视频选择');
  if(route==='/realtime')await shot('实时准备');
  if(route==='/settings'){await shot('设置');assert.equal(await page.locator('.settings-hub a[href="/admin/users"]').count(),1);}
  if(route==='/account'){
   await page.locator('#nickname:not([disabled])').waitFor();
   await shot('个人中心');
   assert.equal(await page.locator('#profile-page a[href="/account/password"]').count(),0);
   assert.equal(await page.locator('#profile-page a[href*="reports=1"]').count(),1);
   await page.locator('#account-menu summary').click();
   assert.deepEqual(await page.locator('.account-dropdown').locator('a,button').allTextContents(),['个人中心','设置','退出登录']);
   await shot('头像菜单');await page.keyboard.press('Escape');
  }
 }
 for(const width of [320,390,768,1024]){
  await page.setViewportSize({width,height:900});
  for(const route of ['/video','/account','/records?view=list','/realtime','/account/ai']){
   await page.goto(fx.origin+route);
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`${route} 在 ${width}px 不应横向溢出`);
  }
  await page.goto(fx.origin+'/records?view=list');await page.locator('#records-ai-review').click();
  await page.locator('#ai-report-options').waitFor({state:'visible'});
  assert.equal(await page.locator('#ai-report-count strong').textContent(),'0');
  assert.equal(await page.locator('#ai-report-generate').isDisabled(),true);
  assert.equal(await page.locator('#ai-report-pagination').isVisible(),false);
  assert.equal(await page.locator('#ai-report-meta').count(),0);
  assert(!/deepseek-flash|供应商账单|应用估算/.test(await page.locator('#ai-report-dialog').innerText()));
  assert(await page.locator('#ai-report-dialog').evaluate(node=>node.scrollWidth<=node.clientWidth),'复盘窗口不应横向溢出');
  if(width===390)await shot('AI复盘-窄屏');
  await page.keyboard.press('Escape');assert.equal(await page.locator('#ai-report-dialog').isVisible(),false);
 }
 await page.setViewportSize({width:1440,height:1000});await page.goto(fx.origin+'/records?view=list');
 await page.locator('#records-ai-review').click();await page.locator('#ai-report-options').waitFor({state:'visible'});await shot('AI复盘');
 assert.equal(await page.evaluate(()=>window.cameraRequests),0);assert.deepEqual(errors,[]);
 console.log('PASS: ten shared headers, three account menu entries, relocated links, four viewport sizes, empty review, Escape, and screenshots.');
}finally{await fx.close();}
