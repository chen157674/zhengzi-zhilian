// 仅检查动效与新增账号页边界，不重跑视频推理或已通过的数据集测试。
import assert from 'node:assert/strict';
import {BrowserFixture} from './formal_browser_support.mjs';
const fx=new BrowserFixture('ui-step21-motion');
try{
 fx.seed(`import os
from fitness_app import create_app
from fitness_app.auth import initialize_admin
app=create_app({'TESTING':True,'DATABASE_PATH':os.environ['FITNESS_DATABASE_PATH']})
initialize_admin(app.extensions['database'],'Motion_UI','ui-motion-test-password')
app.extensions['database'].dispose()
`);
 fx.start();const context=await fx.browser(),page=await context.newPage();page.setDefaultTimeout(15000);await fx.apiLogin(context,'Motion_UI','ui-motion-test-password');
 const second=await fx.browser('Chrome');await fx.apiLogin(second,'Motion_UI','ui-motion-test-password');
 await page.goto(fx.origin+'/account/sessions');await page.waitForFunction(()=>!document.querySelector('#revoke-others').disabled);
 await page.locator('#revoke-others').click();await page.locator('#revoke-password').fill('ui-motion-test-password');await page.locator('#revoke-submit').click();await page.locator('#revoke-dialog').waitFor({state:'hidden'});
 assert.equal((await second.request.get(fx.origin+'/api/v1/auth/me')).status(),401);
 assert.equal((await context.request.get(fx.origin+'/api/v1/auth/me')).status(),200);
 await page.goto(fx.origin+'/account');await page.waitForFunction(()=>!document.querySelector('#nickname').disabled);
 const intervals=await page.evaluate(async()=>{const times=[];let last=null;const menu=document.querySelector('#account-menu');for(let i=0;i<120;i++){const now=await new Promise(requestAnimationFrame);if(last!==null)times.push(now-last);last=now;if(i%12===0)menu.open=!menu.open;}menu.open=false;return times;});
 intervals.sort((a,b)=>a-b);const p95=intervals[Math.floor(intervals.length*.95)];assert(p95<40,'界面帧间隔异常，请检查动效');console.log('Headless UI frame intervals: median='+intervals[Math.floor(intervals.length/2)].toFixed(2)+'ms p95='+p95.toFixed(2)+'ms (not inference FPS)');
 await page.emulateMedia({reducedMotion:'reduce'});await page.reload();assert.equal(await page.locator('main').evaluate(n=>getComputedStyle(n).animationName),'none');
 await page.goto(fx.origin+'/account/password');await page.locator('#current_password').fill('ui-motion-test-password');await page.locator('#new_password').fill('ui-motion-changed-password');await page.locator('#password_confirmation').fill('ui-motion-changed-password');await page.locator('.primary-submit').click();await page.waitForURL('**/login?notice=changed');assert.equal((await context.request.get(fx.origin+'/api/v1/auth/me')).status(),401);
 console.log('PASS: revoke other logins, current retained, password change reauthentication, motion/reduced-motion.');
}finally{await fx.close();}
