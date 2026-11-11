// 真实知识检索与来源页面；不调用供应商，不加载私人数据。
import assert from 'node:assert/strict';
import fs from 'node:fs';import path from 'node:path';
import {BrowserFixture,root} from './formal_browser_support.mjs';
const fx=new BrowserFixture('round5-knowledge'),output=path.join(root,'第五轮优化','实现核对');fs.mkdirSync(output,{recursive:true});const errors=[],cloud=[];
try{
 fx.seed(`from fitness_app import create_app
from fitness_app.auth import initialize_admin
import os
app=create_app({'TESTING':True,'DATABASE_PATH':os.environ['FITNESS_DATABASE_PATH']})
uid=initialize_admin(app.extensions['database'],'Knowledge_Admin','knowledge-fixture-password')['id']
from fitness_app import db_schema as s
client=app.test_client()
token=client.get('/api/v1/auth/csrf').json['data']['csrf_token']
client.post('/api/v1/auth/login',json={'username':'Knowledge_Admin','password':'knowledge-fixture-password'},headers={'X-CSRFToken':token})
with client.session_transaction() as state:auth=state['_user_id']
settings=app.extensions['ai_settings'];settings.environment_key=None
settings.write(uid,auth,{'expected_version':0,'source':'personal','personal_key':'sk-isolated-ui-knowledge-fixture-123456'},s.new_id())
app.extensions['database'].dispose()`);
 await fx.startTestService('tests/formal_roundfive_knowledge_service.py');const context=await fx.browser('Edge',{width:1440,height:1000});await fx.apiLogin(context,'Knowledge_Admin','knowledge-fixture-password');const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(r.method()==='POST'&&/\/api\/v1\/(ai|coach)\//.test(r.url()))cloud.push(r.url());});
 await page.goto(fx.origin+'/coach/knowledge');await page.locator('#knowledge-examples button').first().waitFor();
 await page.locator('#knowledge-query').fill('推胸回程如何控制');let searches=0;await page.route('**/api/v1/knowledge/search',async route=>{if(searches++===0)await route.fulfill({status:503,json:{error:{message:'检索暂时不可用'}}});else await route.continue();});await page.locator('#knowledge-search').click();await page.locator('#knowledge-error').waitFor();await page.locator('#knowledge-retry').click();await page.locator('#knowledge-state').filter({hasText:'已找到参考资料'}).waitFor({timeout:60000});
 await page.locator('#knowledge-clear').click();assert.equal(await page.locator('#knowledge-query').inputValue(),'');assert.equal(await page.locator('#knowledge-result').textContent(),'');await page.locator('#knowledge-query').fill('推胸回程如何控制');await page.locator('#knowledge-search').click();await page.locator('#knowledge-state').filter({hasText:'已找到参考资料'}).waitFor();assert(await page.locator('#knowledge-generate').isEnabled());assert(await page.locator('#knowledge-sources .knowledge-source-row').count()>0);assert.deepEqual(cloud,[]);
 await page.locator('#knowledge-generate').click();await page.locator('#knowledge-state').filter({hasText:'已生成有来源回答'}).waitFor();
 assert(await page.locator('#knowledge-result p a').count()>0);assert.equal(await page.locator('.run-details').count(),0);assert(!(await page.locator('main').innerText()).includes('估算费用'));assert.equal(cloud.length,1);cloud.length=0;const copied=[];await page.evaluate(()=>{window.fixtureCopied='';Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async text=>{window.fixtureCopied=text;}}});});await page.locator('#knowledge-copy').click();assert((await page.evaluate(()=>window.fixtureCopied)).includes('[1]'));assert((await page.evaluate(()=>window.fixtureCopied)).includes('版本'));
 await page.screenshot({path:path.join(output,'知识检索-桌面.png'),fullPage:true});const link=page.locator('#knowledge-sources a').first();await link.click();await page.waitForURL('**/coach/knowledge/*');assert(await page.getByText('资料来源',{exact:true}).isVisible());await page.goBack();await page.locator('#knowledge-query').waitFor();
 await page.setViewportSize({width:390,height:844});await page.locator('#knowledge-query').fill('推胸回程如何控制');await page.locator('#knowledge-search').click();await page.locator('#knowledge-state').filter({hasText:'已找到参考资料'}).waitFor({timeout:60000});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:path.join(output,'知识检索-手机.png'),fullPage:true});
 await page.goto(fx.origin+'/coach/knowledge?view=actions');await page.locator('.guide-action-card').first().waitFor();if(await page.locator('#guide-action-next').isVisible()){await page.locator('#guide-action-next').click();assert((await page.locator('#guide-action-page').textContent()).includes('第 2'));await page.locator('#guide-action-prev').click();}await page.locator('#guide-action-filter').fill('哑铃');assert(await page.locator('.guide-action-card').count()>0);await page.locator('#guide-action-filter').fill('推胸');assert(await page.locator('.guide-action-card').count()>0);await page.locator('.guide-action-card a').first().click();await page.waitForURL('**/actions/*');await page.getByText('当前可用范围',{exact:true}).waitFor();assert(await page.getByRole('link',{name:'开始实时分析',exact:true}).isVisible());assert(await page.getByRole('link',{name:'查询这个动作的知识',exact:true}).isVisible());assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:path.join(output,'动作知识-手机.png'),fullPage:true});
 assert.deepEqual(errors,[]);assert.deepEqual(cloud,[]);console.log('PASS:真实本地检索/来源/目录/动作详情/桌面手机；一项明确供应商替身生成/实际引用，零真实云端/相机。');
}finally{await fx.close();}
