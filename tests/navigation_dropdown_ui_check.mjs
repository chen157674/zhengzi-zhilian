// 两个模块下拉菜单：实际路由、触控/键盘、原离开检查和五入口布局。
import assert from 'node:assert/strict';import fs from 'node:fs';import path from 'node:path';
import {BrowserFixture,root} from './formal_browser_support.mjs';
const fx=new BrowserFixture('navigation-dropdown'),out=path.join(root,'第五轮优化','实现核对'),errors=[],rows=[];
try{
 fx.seed(`import os
from fitness_app import create_app
from fitness_app.auth import initialize_admin
app=create_app({'TESTING':True,'DATABASE_PATH':os.environ['FITNESS_DATABASE_PATH']})
initialize_admin(app.extensions['database'],'Navigation_Admin','navigation-fixture-password')
app.extensions['database'].dispose()`);
 fx.start();const context=await fx.browser('Edge',{width:1440,height:1000});await fx.apiLogin(context,'Navigation_Admin','navigation-fixture-password');const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
 const group=key=>page.locator('[data-nav-group="'+key+'"]');
 const targets=[['guide','学会怎么练','/coach/knowledge'],['guide','饮食建议','/coach/nutrition'],['training','训练计划','/training/plan'],['training','训练记录','/records?view=list'],['training','成长看板','/training/review']];
 for(const width of [1440,1280,1024,900,820,768,430,390,360,320]){
  await page.setViewportSize({width,height:width<768?844:1000});await page.goto(fx.origin+'/coach/nutrition');
  assert.equal(await page.locator('main .module-nav').count(),0);
  assert.equal(await page.locator('.topbar nav > a,.topbar nav > details').count(),5);
  assert(await group('guide').evaluate(n=>n.classList.contains('active')));
  for(const key of ['guide','training']){
   await group(key).locator('summary').click();await page.waitForFunction(k=>document.querySelector('[data-nav-group="'+k+'"]').open,key);
   const panel=await group(key).locator('.nav-group-panel').boundingBox(),nav=await page.locator('.topbar nav').boundingBox(),trigger=await group(key).locator('summary').boundingBox();
   assert(panel&&panel.x>=0&&panel.x+panel.width<=width+1,key+' menu horizontal bounds '+width);
   assert(panel.y>=0&&panel.y+panel.height<= (width<768?nav.y+1:1000),key+' menu viewport '+width);
   if(width>=768)assert(panel.y>=trigger.y+trigger.height,key+' desktop opens down');
   assert.equal(await page.locator('[data-nav-group][open]').count(),1);
   assert.equal(await group(key).locator('summary').getAttribute('aria-expanded'),'true');
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'page overflow '+width);
   rows.push({width,key,opens:width<768?'up':'down',bounds:panel});
   if(key==='guide'&&[1440,390].includes(width)){await page.evaluate(()=>scrollTo({top:0,behavior:'instant'}));await page.screenshot({path:path.join(out,'导航下拉-'+(width===1440?'桌面':'手机')+'.png'),animations:'disabled'});}
  }
  await page.keyboard.press('Escape');assert.equal(await page.locator('[data-nav-group][open]').count(),0);
 }
 await page.setViewportSize({width:1440,height:1000});
 for(const [key,label,route]of targets){await group(key).locator('summary').click();await group(key).getByRole('link',{name:label,exact:true}).click();await page.waitForURL(url=>{const expected=new URL(fx.origin+route);return url.pathname===expected.pathname&&[...expected.searchParams].every(([k,v])=>url.searchParams.get(k)===v);});assert.equal(await page.locator('main .module-nav').count(),0);assert(await group(key).evaluate(n=>n.classList.contains('active')));assert.equal(await group(key).locator('a[aria-current=page]').getAttribute('href'),route);}
 await page.goto(fx.origin+'/coach/knowledge');await group('guide').locator('summary').focus();await page.keyboard.press('ArrowDown');assert(await group('guide').getByRole('link',{name:'学会怎么练',exact:true}).evaluate(n=>n===document.activeElement));await page.keyboard.press('End');assert(await group('guide').getByRole('link',{name:'饮食建议',exact:true}).evaluate(n=>n===document.activeElement));await page.keyboard.press('Home');await page.keyboard.press('ArrowDown');await page.keyboard.press('Escape');assert(await group('guide').locator('summary').evaluate(n=>n===document.activeElement));
 await group('guide').locator('summary').click();await page.locator('main h1').click();assert.equal(await page.locator('[data-nav-group][open]').count(),0);
 await page.evaluate(()=>{window.fitnessLeaveGuard=async()=>false;});await group('guide').locator('summary').click();await group('guide').getByRole('link',{name:'饮食建议',exact:true}).click();await page.waitForTimeout(140);assert.equal(new URL(page.url()).pathname,'/coach/knowledge');await page.evaluate(()=>{window.fitnessLeaveGuard=async()=>true;});await group('guide').locator('summary').click();await group('guide').getByRole('link',{name:'饮食建议',exact:true}).click();await page.waitForURL(fx.origin+'/coach/nutrition');
 await context.clearCookies();const publicPage=await context.newPage();await publicPage.setViewportSize({width:390,height:844});for(const route of ['/login','/register']){await publicPage.goto(fx.origin+route);assert.equal(await publicPage.locator('[data-nav-group]').count(),0);}
 assert.deepEqual(errors,[]);fs.writeFileSync(path.join(out,'导航下拉核对.json'),JSON.stringify({rows,errors,targets:targets.map(x=>x[2]),keyboard:true,outside_dismiss:true,mutually_exclusive:true,leave_guard:true,no_duplicate_module_tabs:true,public_auth_without_menus:true,result:'passed'},null,2));
 console.log('PASS:两组下拉×10档宽度、5个子页、选中态、键盘/外点关闭、原离开检查；无页面内重复子导航。');
}finally{await fx.close();}
