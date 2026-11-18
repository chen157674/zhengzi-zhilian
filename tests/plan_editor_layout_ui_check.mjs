// 在隔离数据库验证编辑布局、真实提交及响应式；不调用云端 AI。
import assert from 'node:assert/strict';
import path from 'node:path';
import {BrowserFixture, root} from './formal_browser_support.mjs';

const fx = new BrowserFixture('plan-editor-layout');
const out = path.join(root, '第五轮优化', '实现核对');
const errors = [];
try {
  fx.seed(`from fitness_app import create_app
from fitness_app.auth import initialize_admin
import os
app=create_app({'TESTING':True,'DATABASE_PATH':os.environ['FITNESS_DATABASE_PATH']})
initialize_admin(app.extensions['database'],'Editor_Admin','editor-fixture-password')
app.extensions['database'].dispose()`);
  fx.start();
  const context = await fx.browser('Edge', {width:1440,height:1000});
  await fx.apiLogin(context, 'Editor_Admin', 'editor-fixture-password');
  const page = await context.newPage();
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(fx.origin + '/training/plans/new');
  await page.locator('#plan-editor').waitFor();
  await page.locator('#plan-name').fill('每周三练 · 全身力量');
  await page.locator('#plan-goal').selectOption('strength');
  await page.getByLabel('训练动作', {exact:true}).selectOption('bodyweight_squat');
  await page.getByLabel('组数', {exact:true}).fill('3');
  await page.locator('#plan-equipment-choices').getByLabel('哑铃', {exact:true}).check();
  assert.match(await page.locator('.plan-preview-title').textContent(), /每周三练/);
  assert.match(await page.locator('.plan-preview-meta').textContent(), /力量/);
  assert.deepEqual(await page.locator('#plan-equipment').evaluate(el => [...el.selectedOptions].map(o=>o.value)), ['dumbbell']);
  for (const width of [1440,1280,1024,800,768,414,390,375,320]) {
    await page.setViewportSize({width,height:width < 768 ? 844 : 1000});
    assert(await page.evaluate(()=>document.documentElement.scrollWidth <= innerWidth+1), '页面溢出 '+width);
    const fields = await page.locator('#plan-editor input:not([hidden]):not([type=checkbox]), #plan-editor select:not([hidden])').evaluateAll(elements =>
      elements.filter(el=>el.getClientRects().length).map(el=>({left:el.getBoundingClientRect().left,right:el.getBoundingClientRect().right})));
    assert(fields.every(rect=>rect.left>=0&&rect.right<=width+1),'输入控件被裁切 '+width);
    if (width===1440 || width===390) {
      await page.evaluate(()=>{document.activeElement?.blur();scrollTo(0,0);});
      await page.screenshot({path:path.join(out,'训练计划表单-'+(width===1440?'桌面':'手机')+'.jpg'),fullPage:true,type:'jpeg',quality:75});
    }
  }
  await page.setViewportSize({width:1440,height:1000});
  await page.getByRole('button',{name:'＋ 添加动作',exact:true}).click();
  assert.equal(await page.locator('.plan-item-editor').count(),2);
  await page.getByLabel('训练动作',{exact:true}).nth(1).selectOption('standing_dumbbell_curl');
  await page.getByRole('button',{name:'上移',exact:true}).nth(1).click();
  assert.equal(await page.getByLabel('训练动作',{exact:true}).first().inputValue(),'standing_dumbbell_curl');
  await page.locator('.plan-item-options summary').first().click();
  await page.getByLabel('负重（可选）',{exact:true}).first().fill('5');
  await page.locator('.plan-preferences summary').click();
  await page.locator('#plan-preferences').fill('偏好哑铃训练');
  await page.locator('#plan-add-day').click();
  assert.equal(await page.locator('.plan-session-editor').count(),2);
  assert.equal(await page.locator('.plan-preview-day').count(),2);
  await page.locator('#plan-ai-options summary').click();
  await page.locator('#plan-weekday-choices').getByLabel('周二',{exact:true}).check();
  assert(await page.locator('#plan-ai-generate').isVisible());
  await page.locator('#plan-ai-options summary').click();
  await page.locator('#plan-save').click();
  await page.waitForURL('**/activate');
  await page.locator('#plan-confirm-content h2').waitFor();
  assert.match(await page.locator('#plan-confirm-content').textContent(),/每周三练/);
  await page.locator('#plan-back-edit').click();
  await page.locator('#plan-editor').waitFor();
  assert.equal(await page.locator('#plan-preferences').inputValue(),'偏好哑铃训练');
  assert(await page.locator('.plan-preferences').getAttribute('open') !== null);
  assert.equal(await page.getByLabel('负重（可选）',{exact:true}).first().inputValue(),'5');
  await page.locator('#plan-save').click();
  await page.waitForURL('**/activate');
  await page.locator('#plan-activate').click();
  await page.waitForURL('**/training/plan');
  await page.locator('#plan-day-card').waitFor();
  assert.match(await page.locator('#plan-title').textContent(),/每周三练/);
  assert.deepEqual(errors,[]);
  console.log('PASS: 9种宽度无溢出；摘要、器械、动作排序、负重、偏好、增加日期、AI展开、保存回读及启用；零浏览器错误。');
} finally {
  await fx.close();
}
