import assert from 'node:assert/strict';
import path from 'node:path';
import {BrowserFixture,root} from './formal_browser_support.mjs';
const pageMode=process.env.FITNESS_COACH_UI_CONTAINER==='page';
const fx=new BrowserFixture('coach-ui');fx.env.APPDATA=path.join(fx.temporary,'roaming');fx.env.LOCALAPPDATA=path.join(fx.temporary,'localappdata');
try{
 fx.seed(`import os
from fitness_app import create_app
from fitness_app.auth import initialize_admin
app=create_app({'TESTING':True,'DATABASE_PATH':os.environ['FITNESS_DATABASE_PATH']})
initialize_admin(app.extensions['database'],'Coach_Admin','coach-ui-password')
app.extensions['database'].dispose()`);
 await fx.startTestService('tests/formal_coach_service.py');const context=await fx.browser('Edge');await fx.apiLogin(context,'Coach_Admin','coach-ui-password');
 const csrf=(await(await context.request.get(fx.origin+'/api/v1/auth/csrf')).json()).data.csrf_token;
 const settings=await context.request.patch(fx.origin+'/api/v1/me/ai-settings',{headers:{'X-CSRFToken':csrf,'Idempotency-Key':crypto.randomUUID()},data:{expected_version:0,source:'personal',personal_key:'sk-coach-ui-protocol-fixture-123456'}});assert.equal(settings.status(),200);
 const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.setViewportSize({width:390,height:900});await page.goto(fx.origin+(pageMode?'/coach':'/realtime'));if(pageMode)await page.locator('#coach-nutrition-entry').click();else{await page.locator('[data-ai-open]').click();await page.locator('#nutrition-panel>summary').click();}
 await page.locator('#nutrition-description-text').fill('我25岁，70公斤，男，规律健身，整体轻活动，估算蛋白质和热量。');
 await page.locator('#nutrition-description-send').click();await page.getByText('请先确认本次描述发送给 DeepSeek。',{exact:true}).waitFor();
 await page.locator('#nutrition-cloud-consent').check();await page.locator('#nutrition-description-send').click();
 await page.getByText('95—140 克/天',{exact:true}).waitFor();assert(await page.locator('#nutrition-coach-answer').innerText().then(x=>x.includes('已按本次自述')));
 await page.locator('#nutrition-save-profile').click();await page.locator('#confirm-dialog').waitFor();await page.locator('[data-dialog-confirm]').click();
 await page.getByText('当前资料已保存。描述和结果未保存。',{exact:true}).waitFor();
 await page.screenshot({path:path.join(root,'第四轮优化/实现核对',pageMode?'AI饮食参考-独立页.png':'AI饮食参考-侧栏.png'),fullPage:true,animations:'disabled'});
 if(pageMode){await page.reload();await page.locator('#coach-nutrition-entry').click();}else{await page.locator('#ai-assistant-close').click();await page.locator('[data-ai-open]').click();}await page.locator('#nutrition-use-profile').click();await page.getByText('已载入你保存的资料，可修改后估算。',{exact:true}).waitFor();
 assert.equal(await page.locator('[name="weight"]').inputValue(),'70');assert(await page.locator('#nutrition-form').isVisible());
 await page.locator('#nutrition-delete-profile').click();await page.locator('[data-dialog-confirm]').click();await page.getByText('已清除身体资料。',{exact:true}).waitFor();
 assert.equal(await page.locator('[name="weight"]').inputValue(),'');assert.equal(await page.locator('#nutrition-results').textContent(),'');
 await page.reload();if(!pageMode)await page.locator('[data-ai-open]').click();assert.equal(await page.locator('#nutrition-description-text').inputValue(),'');
 assert.deepEqual(errors,[]);console.log('PASS real API routes + stub tool loop, consent, result, explicit profile save/reuse/delete, refresh clears.');
 await context.close();fx.contexts=[];console.log(JSON.stringify({temporary:fx.temporary,origin:fx.origin}));await fx.close();
}catch(error){await fx.close();throw error;}
