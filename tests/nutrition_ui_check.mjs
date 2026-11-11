import assert from 'node:assert/strict';
import path from 'node:path';
import {BrowserFixture} from './formal_browser_support.mjs';
const fx=new BrowserFixture('nutrition-ui');
try{
 fx.seed(`import os
from fitness_app import create_app
from fitness_app.auth import initialize_admin
app=create_app({'TESTING':True,'DATABASE_PATH':os.environ['FITNESS_DATABASE_PATH']})
initialize_admin(app.extensions['database'],'Nutrition_Admin','nutrition-ui-password')
app.extensions['database'].dispose()`);
 fx.env.APPDATA=path.join(fx.temporary,'roaming');fx.env.LOCALAPPDATA=path.join(fx.temporary,'localappdata');
 fx.start();const context=await fx.browser('Edge');await fx.apiLogin(context,'Nutrition_Admin','nutrition-ui-password');
 const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
 for(const width of [1440,390,320]){
  await page.setViewportSize({width,height:900});await page.goto(fx.origin+'/realtime');
  await page.locator('[data-ai-open]').first().click();await page.locator('#nutrition-panel>summary').click();await page.locator('#nutrition-tab-form').click();
  await page.locator('[name="age_years"]').fill('25');await page.locator('[name="weight"]').fill('70');
  await page.locator('[name="exercise_status"]').selectOption('regular');await page.locator('[name="formula_sex"]').selectOption('male');
  await page.locator('[name="activity_category"]').selectOption('light');await page.locator('#nutrition-form [type="submit"]').click();
  await page.getByText('95—140 克/天',{exact:true}).waitFor();await page.getByText('2400—3000 千卡/天',{exact:true}).waitFor();
  assert(await page.locator('#ai-assistant').evaluate(n=>n.scrollWidth<=n.clientWidth),'侧栏不得横向溢出');
  assert.equal(await page.evaluate(()=>[...Object.values(localStorage),...Object.values(sessionStorage)].some(x=>/weight|70|2400/.test(x))),false);
  await page.locator('#nutrition-results details').first().locator('summary').click();
  await page.screenshot({path:path.join(fx.temporary,`nutrition-${width}.png`),animations:'disabled'});
  await page.locator('[name="weight"]').fill('80');assert.equal(await page.locator('#nutrition-results').textContent(),'');
  await page.locator('#nutrition-clear').click();assert.equal(await page.locator('[name="weight"]').inputValue(),'');
  await page.locator('[name="weight"]').fill('70');await page.locator('#ai-assistant-close').click();
  assert.equal(await page.locator('[name="weight"]').inputValue(),'');
 }
 assert.deepEqual(errors,[]);console.log('PASS desktop/mobile formula, source details, edit invalidation, clear/close, no persistent browser values.');
 console.log('Screenshots in '+fx.temporary);await context.close();fx.contexts=[];
 // 截图仅本轮视觉核查；正常运行结束清理服务和归属目录。
 console.log(JSON.stringify({origin:fx.origin,temporary:fx.temporary}));await fx.close();
}catch(error){await fx.close();throw error;}
