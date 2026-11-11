// 最终变动范围：认证双栏、首页素材、管理表格与错误页的实际截图。
import path from 'node:path';import assert from 'node:assert/strict';
import {BrowserFixture,root} from './formal_browser_support.mjs';
const fx=new BrowserFixture('ui-step21-finish'),out=path.join(root,'前端重设计/实现核对');
try{fx.seed(`import os
from fitness_app import create_app
from fitness_app.auth import initialize_admin
app=create_app({'TESTING':True,'DATABASE_PATH':os.environ['FITNESS_DATABASE_PATH']})
initialize_admin(app.extensions['database'],'Visual_UI','ui-visual-test-password')
app.extensions['database'].dispose()
`);fx.start();const context=await fx.browser('Edge',{width:1536,height:1024}),page=await context.newPage();page.setDefaultTimeout(15000);const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const shot=async(name)=>{await page.evaluate(()=>scrollTo(0,0));await page.evaluate(()=>document.fonts.ready);await page.waitForTimeout(250);await page.screenshot({path:path.join(out,name+'.png'),fullPage:true,animations:'disabled'});};
 if(!process.argv.includes('--home-only')){await page.goto(fx.origin+'/login');await page.locator('.auth-visual img').evaluate(img=>img.decode());const photo=await page.locator('.auth-visual img').boundingBox(),form=await page.locator('.auth-form-panel').boundingBox();assert(Math.abs(photo.x-116)<8);assert(Math.abs(photo.width-706)<8);assert(Math.abs(form.x-944)<8);await shot('登录');
 await page.goto(fx.origin+'/register');await shot('注册');
 await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));await shot('注册-窄屏');
 await fx.apiLogin(context,'Visual_UI','ui-visual-test-password');await page.setViewportSize({width:1536,height:1024});
 await page.goto(fx.origin+'/account');await page.waitForFunction(()=>!document.querySelector('#nickname').disabled);const editor=await page.locator('.profile-editor').boundingBox();assert(Math.abs(editor.x+editor.width-842)<8);await shot('个人中心');
 await page.goto(fx.origin+'/admin/users');await page.waitForFunction(()=>document.querySelector('#account-rows').textContent.includes('Visual_UI'));assert((await page.locator('thead').textContent()).includes('密码状态'));await shot('账号管理');
 }else{await fx.apiLogin(context,'Visual_UI','ui-visual-test-password');}
 await page.setViewportSize({width:1024,height:900});await page.goto(fx.origin+'/');await page.waitForTimeout(500);assert.equal(Math.round((await page.locator('#home-demo').boundingBox()).width),928);await shot('首页');
 await page.goto(fx.origin+'/unknown-page');await shot('页面不存在');assert.deepEqual(errors,[]);
 console.log('PASS: adopted-image anchor geometry, final assets, account/admin and responsive/error screenshots.');
}finally{await fx.close();}
