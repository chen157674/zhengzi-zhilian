// 更名后的实际页面和标识检查，仅项目内隔离账号，不调用真实AI。
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {BrowserFixture,root} from './formal_browser_support.mjs';

const fixture=new BrowserFixture('brand-rename');
const out=path.join(root,'第五轮优化/实现核对');
const errors=[],checks=[];
try{
 fixture.seed(`import os
from fitness_app import create_app
from fitness_app.auth import initialize_admin
from scripts.seed_review_account import seed
from fitness_app.knowledge_store import KnowledgeStore
app=create_app({'TESTING':True,'DATABASE_PATH':os.environ['FITNESS_DATABASE_PATH']})
initialize_admin(app.extensions['database'],'Rename_Admin','rename-admin-password')
seed(os.environ['FITNESS_DATABASE_PATH'],True,'rename-review-password')
assert KnowledgeStore().index_status=='hybrid_ready'
for hook in app.extensions.get('stop_hooks',[]):hook()
app.extensions['database'].dispose()`);
 fixture.start();const context=await fixture.browser('Edge',{width:1440,height:1000});
 await fixture.apiLogin(context,'review_judge','rename-review-password');
 const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
 let cloud=0;page.on('request',r=>{if(r.method()==='POST'&&/\/(ai\/conversations|coach\/tasks|plan-jobs|review-jobs)$/.test(new URL(r.url()).pathname))cloud++;});
 const pages=[
  ['/','首页下滑优化-桌面.png',null],
  ['/training/plan','训练计划-桌面.png','#plan-overview:not([hidden])'],
  ['/coach','AI教练.png','#ai-chat-input'],
  ['/coach/knowledge','知识检索-桌面.png',null],
  ['/coach/nutrition','饮食建议-桌面.png',null],
  ['/training/review','评审账号-成长看板.png','#review-page[data-growth-state=ready]'],
 ];
 for(const [route,file,ready]of pages){
  await page.goto(fixture.origin+route,{waitUntil:'networkidle'});
  if(ready)await page.locator(ready).waitFor();
  if(route==='/training/plan'&&await page.locator('#plan-days [data-state=planned]').count()){
   await page.locator('#plan-days [data-state=planned]').first().click();
   await page.locator('#plan-day-card[data-state=planned]').waitFor();
  }
  if(route==='/training/review'){
   await page.locator('#review-prev-week').click();
   await page.locator('#review-page[data-growth-state=ready]').waitFor();
  }
  await page.evaluate(async()=>{await document.fonts.ready;await Promise.all([...document.images].filter(x=>{const r=x.getBoundingClientRect();return r.width&&r.height&&r.top<innerHeight&&r.bottom>0;}).map(x=>x.decode().catch(()=>{})));});
  assert(!(await page.title()).includes('知练同行'));
  if(route==='/')assert.match(await page.title(),/正姿智练/);
  assert.equal(await page.locator('.brand').getAttribute('aria-label'),'正姿智练首页');
  assert((await page.locator('.brand img').evaluate(x=>x.naturalWidth))>0);
  assert(!await page.locator('#main').textContent().then(t=>t.includes('知练同行')));
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  await page.screenshot({path:path.join(out,file),fullPage:true,animations:'disabled'});
  if(route==='/')await page.locator('.brand').screenshot({path:path.join(out,'项目更名-标识.png')});
  checks.push({route,width:1440,new_brand:true});
 }
 await page.setViewportSize({width:390,height:844});
 await page.goto(fixture.origin+'/',{waitUntil:'networkidle'});
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
 assert.equal(await page.locator('.brand').getAttribute('aria-label'),'正姿智练首页');
 await page.screenshot({path:path.join(out,'项目更名-手机.png'),animations:'disabled'});
 checks.push({route:'/',width:390,new_brand:true});
 await context.clearCookies();
 await page.setViewportSize({width:1440,height:1000});
 await page.goto(fixture.origin+'/login',{waitUntil:'networkidle'});
 assert.match(await page.title(),/正姿智练/);
 assert.match(await page.locator('.auth-form-panel').textContent(),/登录正姿智练/);
 assert((await page.locator('.auth-form-brand img').evaluate(x=>x.naturalWidth))>0);
 await page.screenshot({path:path.join(out,'项目更名-登录页.png'),animations:'disabled'});
 assert.deepEqual(errors,[]);assert.equal(cloud,0);
 fs.writeFileSync(path.join(out,'项目更名核对.json'),JSON.stringify({name:'正姿智练',full_name:'正姿智练——基于姿态感知与大模型协同的健身动作纠错与指导平台',checks,login_brand:true,errors,cloud_requests:cloud},null,2)+'\n');
 console.log(JSON.stringify({branded_pages:checks.length,login:true,script_errors:0,cloud_requests:0}));
}finally{await fixture.close();}
