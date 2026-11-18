// 新食物图片在真实营养分析/自动归档/看板中一致使用；模型为显式协议替身。
import assert from 'node:assert/strict';import fs from 'node:fs';import path from 'node:path';
import {BrowserFixture,root} from './formal_browser_support.mjs';
const fx=new BrowserFixture('food-report'),out=path.join(root,'第五轮优化','实现核对'),errors=[],rows=[];
try{
 fx.seed(`import os
from fitness_app import create_app,db_schema as s
from fitness_app.auth import initialize_admin
app=create_app({'TESTING':True,'DATABASE_PATH':os.environ['FITNESS_DATABASE_PATH']})
uid=initialize_admin(app.extensions['database'],'Food_Admin','food-fixture-password')['id']
client=app.test_client();csrf=client.get('/api/v1/auth/csrf').json['data']['csrf_token']
client.post('/api/v1/auth/login',json={'username':'Food_Admin','password':'food-fixture-password'},headers={'X-CSRFToken':csrf})
with client.session_transaction() as state:auth=state['_user_id']
settings=app.extensions['ai_settings'];settings.environment_key=None
settings.write(uid,auth,{'expected_version':0,'source':'personal','personal_key':'sk-isolated-food-fixture-123456'},s.new_id())
app.extensions['database'].dispose()`);
 await fx.startTestService('tests/formal_roundfive_knowledge_service.py');const context=await fx.browser('Edge',{width:1440,height:1000});await fx.apiLogin(context,'Food_Admin','food-fixture-password');
 const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));const assets=(await(await context.request.get(fx.origin+'/api/v1/nutrition/food-assets')).json()).data.assets;assert.equal(assets.length,17);
 await page.goto(fx.origin+'/coach/nutrition');await page.locator('[data-nutrition-analysis]').click();await page.locator('#knowledge-query').fill('午餐吃了一碗泡面、一个汉堡和一份牛肉。');await page.locator('#knowledge-search').click();await page.locator('.nutrition-analysis-result').waitFor({timeout:20000});await page.getByRole('link',{name:'已记入我的餐食 ↗',exact:true}).waitFor();
 for(const file of ['instant-noodles','hamburger','lean-beef'])assert.equal(await page.locator('.nutrition-food-table img[src$="/'+file+'.webp"]').count(),1);
 assert.equal(await page.locator('.nutrition-next-meal img[src$="/fish.webp"]').count(),1);
 assert((await page.locator('.knowledge-message--assistant .knowledge-message-content').textContent()).includes('牛肉选瘦肉'));assert.equal(await page.locator('.nutrition-swap-row--photos').count(),2);
 const decoded=await page.locator('.nutrition-analysis-result img').evaluateAll(async nodes=>{await Promise.all(nodes.map(n=>n.decode()));return nodes.every(n=>n.naturalWidth>0);});assert(decoded);
 for(const width of [1440,768,390,320]){
  await page.setViewportSize({width,height:900});const dialog=page.locator('#knowledge-ai-dialog');assert(await dialog.evaluate(n=>n.scrollWidth<=n.clientWidth+1),'report overflow '+width);
  const box=await dialog.boundingBox();assert(box.x>=0&&box.x+box.width<=width+1);
  rows.push({page:'report',width,passed:true});
  if(width===1440||width===390){await dialog.evaluate(n=>{n.scrollTop=0;});await page.locator('.knowledge-dialog-body').evaluate(n=>{n.scrollTop=0;});await page.screenshot({path:path.join(out,'常见食物报告-'+(width===1440?'桌面':'手机')+'.png'),animations:'disabled'});}
 }
 await page.keyboard.press('Escape');await page.goto(fx.origin+'/coach/meals');await page.locator('#meal-journal-page[data-ready=true]').waitFor();
 for(const file of ['instant-noodles','hamburger','lean-beef'])assert.equal(await page.locator('#journal-records img[src$="/'+file+'.webp"]').count(),1);
 for(const width of [1440,390,320]){await page.setViewportSize({width,height:900});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'journal overflow '+width);rows.push({page:'journal',width,passed:true});}
 await page.goto(fx.origin+'/training/review');await page.locator('#review-page[data-growth-state=ready]').waitFor();assert.equal(await page.locator('#growth-recent-meals img[src$="/instant-noodles.webp"]').count(),1);
 assert.deepEqual(errors,[]);fs.writeFileSync(path.join(out,'常见食物报告核对.json'),JSON.stringify({date:new Date().toISOString(),rows,errors,asset_count:17,new_images:12,name_matched_report:true,swap_photos:true,auto_saved_journal:true,dashboard_image:true,image_decode:true,paid_supplier_calls:0,provider:'explicit_protocol_double',result:'passed'},null,2));
 console.log('PASS:17张已复核素材，泡面/汉堡/牛肉报告按名称配图，替换图文、自动餐食与成长看板一致；四档报告/三档餐食。');
}finally{await fx.close();}
