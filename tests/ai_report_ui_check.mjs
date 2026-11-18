// 实际页面、本人API、后台任务及持久化；供应商替身，不作为真实AI或人体效果证明。
import assert from 'node:assert/strict';import fs from 'node:fs';
import path from 'node:path';
import {BrowserFixture,root} from './formal_browser_support.mjs';
const output=path.resolve(root,process.env.FITNESS_UI_EVIDENCE_DIR||'第四轮优化/实现核对');assert(!path.relative(root,output).startsWith('..'));fs.mkdirSync(output,{recursive:true});
const fixture=new BrowserFixture('round3-ai-report-ui'),errors=[],mode=process.env.FITNESS_AI_REPORT_UI_SCOPE||'full';
try{
 const seeded=JSON.parse(fixture.seed(`
import os,sys,json
from types import SimpleNamespace
from sqlalchemy import select
from fitness_app.paths import ROOT,configure_environment
configure_environment();sys.path.insert(0,str(ROOT/'tests'))
from fitness_app.app import create_app
from fitness_app.auth import initialize_admin
from fitness_app import db_schema as s
from test_pdf_report import PdfRenderTests
app=create_app({'TESTING':True,'DATABASE_PATH':os.environ['FITNESS_DATABASE_PATH']});db=app.extensions['database'];initialize_admin(db,'AI_Review_Admin','AI-review-fixture-2026')
c=app.test_client();token=c.get('/api/v1/auth/csrf').json['data']['csrf_token'];assert c.post('/api/v1/auth/login',json={'username':'AI_Review_Admin','password':'AI-review-fixture-2026'},headers={'X-CSRFToken':token}).status_code==200
with c.session_transaction() as state:auth=state['_user_id']
with db.transaction(False) as conn:uid=conn.scalar(select(s.auth_sessions.c.user_id).where(s.auth_sessions.c.id==auth))
app.extensions['ai_settings'].write(uid,auth,{'source':'personal','personal_key':'sk-isolated-ui-fixture-123456789','expected_version':0},s.new_id())
store=app.extensions['session_store'];helper=PdfRenderTests();helper.fx=SimpleNamespace(store=store,user=uid,auth=auth,finish=lambda sid:store.set_state(uid,sid,'ended_early',s.new_id(),auth_id=auth,reason='ui_fixture_end'))
print(json.dumps({'first':helper.record('user_selected'),'second':helper.record('automatically_recognized')}));db.dispose()
`));
 await fixture.startTestService('tests/formal_ai_report_service.py');const browser=await fixture.browser('Edge',{width:390,height:844});await fixture.apiLogin(browser,'AI_Review_Admin','AI-review-fixture-2026');
 await browser.addInitScript(()=>{window.cameraRequests=0;if(navigator.mediaDevices)navigator.mediaDevices.getUserMedia=async()=>{window.cameraRequests++;throw new Error('No camera in AI report check');};});
 const page=await browser.newPage();page.on('pageerror',e=>errors.push(String(e)));const dialog=page.locator('#ai-report-dialog');
 const calls=async()=>(await(await browser.request.get(fixture.origin+'/api/v1/_fixture/ai-stats')).json()).data.calls;
 const reports=async()=>(await(await browser.request.get(fixture.origin+'/api/v1/ai/reports')).json()).data.reports;
 const post=async(url,data)=>{const csrf=(await(await browser.request.get(fixture.origin+'/api/v1/auth/csrf')).json()).data.csrf_token;return browser.request.post(fixture.origin+url,{data,headers:{'X-CSRFToken':csrf,'Idempotency-Key':crypto.randomUUID()}});};
 const entry=async()=>{await page.goto(fixture.origin+'/records/'+seeded.first);await page.locator('#record-ai-review:not([disabled])').waitFor();await page.locator('#record-ai-review').click();await page.locator('#ai-report-options').waitFor({state:'visible'});};
 const generate=async()=>{await page.locator('#ai-report-consent').check();await page.locator('#ai-report-generate').click();await page.locator('.ai-report-summary').waitFor();};
 const screenshot=async name=>{await page.evaluate(async()=>await Promise.all(document.getAnimations().filter(a=>a.effect?.getTiming().iterations!==Infinity).map(a=>a.finished.catch(()=>{}))));await page.screenshot({path:path.join(output,name),animations:'disabled'});};
 if(['pending','missing'].includes(mode)){
  let submitted=0;const submittedIds=[];await page.route('**/api/v1/ai/reports',async route=>{if(route.request().method()==='POST'){submittedIds.push(route.request().headers()['idempotency-key']);if(submitted++===0){if(mode==='pending')await route.fetch();await route.fulfill({status:503,json:{error:{message:'工程替身：提交回执丢失'}}});return;}}await route.continue();});
  await entry();await page.locator('#ai-report-consent').check();await page.locator('#ai-report-generate').click();await page.locator('#ai-report-pending').waitFor({state:'visible'});
  await page.reload();await page.locator('#record-ai-review:not([disabled])').waitFor();await page.locator('#record-ai-review').click();await page.locator('#ai-report-pending').waitFor({state:'visible'});await page.locator('#ai-report-check').click();
  if(mode==='missing'){await page.locator('#ai-report-resend').waitFor({state:'visible'});assert.equal(await calls(),0);assert.equal(submitted,1);assert.match(await page.locator('#ai-report-scope').textContent(),/原请求/);await page.locator('#ai-report-consent').check();await page.locator('#ai-report-resend').click();}
  await page.locator('.ai-report-summary').waitFor();assert.equal(submitted,mode==='missing'?2:1);assert.equal(await calls(),1);assert.equal(new Set(submittedIds).size,1);console.log(mode==='missing'?'Missing request→query only→explicit reconfirmation→retry original ID, one actual provider call: passed.':'Lost creation receipt→refresh→query original request→saved result, one provider call and one POST: passed.');
 }else if(mode==='late'){
  await entry();await generate();const saved=(await reports())[0];await page.locator('#ai-report-back').click();await page.locator('#record-ai-review').click();await page.locator('#ai-report-options').waitFor({state:'visible'});
  let release,waiting=false;const gate=new Promise(resolve=>release=resolve);await page.route('**/api/v1/ai/reports/'+saved.request_id,async route=>{const reply=await route.fetch();waiting=true;await gate;await route.fulfill({response:reply});});
  await page.locator('#ai-report-dialog .report-export-history:not([open])>summary').click();await page.locator('#ai-report-history .report-history-row').first().click();while(!waiting)await page.waitForTimeout(25);
  await page.locator('#ai-report-back').click();await page.locator('#record-ai-review').click();await page.locator('#ai-report-options').waitFor({state:'visible'});release();await page.waitForTimeout(150);
  assert.equal(await page.locator('#ai-report-options').isVisible(),true);assert.equal(await page.locator('#ai-report-result').isVisible(),false);assert.equal(await calls(),1);
  await page.evaluate(()=>{const set=Storage.prototype.setItem;Storage.prototype.setItem=function(key,value){if(key.startsWith('fitness-ai-report-pending:'))throw new DOMException('fixture storage blocked','QuotaExceededError');return set.call(this,key,value);};});
  await page.locator('#ai-report-consent').check();await page.locator('#ai-report-generate').click();await page.locator('#ai-report-error').filter({hasText:'fixture storage blocked'}).waitFor();assert.equal(await calls(),1);console.log('Late old detail cannot overwrite reopened range; pending-ID storage failure prevents new AI submission: passed.');
 }else if(mode==='source-delete'){
  await entry();await generate();const saved=(await reports())[0];await page.locator('#ai-report-back').click();
  const record=(await(await browser.request.get(fixture.origin+'/api/v1/records/'+seeded.first)).json()).data;
  const removed=await post('/api/v1/records/delete',{ids:[seeded.first],expected_versions:{[seeded.first]:record.state_version}});assert.equal(removed.status(),200);
  await page.goto(fixture.origin+'/records?view=list');await page.locator('#records-ai-review').click();await page.locator('#ai-report-dialog .report-export-history:not([open])>summary').click();await page.locator('#ai-report-history .report-history-row').filter({hasText:'训练复盘'}).first().click();
  await page.locator('#ai-report-answer').filter({hasText:'来源训练已删除，这份复盘已失效'}).waitFor();assert.equal(await page.locator('.ai-report-summary').count(),0);assert.equal(await calls(),1);
  const row=(await(await browser.request.get(fixture.origin+'/api/v1/ai/reports/'+saved.request_id)).json()).data;assert.equal(row.result,null);assert.equal(row.error_code,'AI_SOURCE_DELETED');assert(row.estimated_micro_cny>0);console.log('Record deletion clears persisted AI body; history displays source-removal reason, retains actual usage and never regenerates: passed.');
 }else if(mode==='list-scope'){
  await page.goto(fixture.origin+'/records?view=list&source=video');await page.locator('#records-ai-review').click();await page.locator('#ai-report-options').waitFor({state:'visible'});assert.match(await page.locator('#ai-report-scope').textContent(),/全部训练记录/);assert.match(await page.locator('#ai-report-read-scope').textContent(),/参考 2 条历史记录/);assert.equal(await calls(),0);console.log('No selection explicitly previews all owned effective history independently of list-only filters, no AI call: passed.');
 }else if(mode==='basis'){
  await entry();await generate();assert.equal(await page.locator('#ai-report-answer .ai-report-sources a').getAttribute('href'),'/records/'+seeded.first);await page.locator('#ai-report-back').click();
  const settings=(await(await browser.request.get(fixture.origin+'/api/v1/me/ai-settings')).json()).data,csrf=(await(await browser.request.get(fixture.origin+'/api/v1/auth/csrf')).json()).data.csrf_token;
  const change=await browser.request.patch(fixture.origin+'/api/v1/me/ai-settings',{data:{expected_version:settings.version,history_reference:false},headers:{'X-CSRFToken':csrf,'Idempotency-Key':crypto.randomUUID()}});assert.equal(change.status(),200);
  await page.locator('#record-ai-review').click();await page.locator('#ai-report-options').waitFor({state:'visible'});assert.equal(await page.locator('#ai-report-history-reference').isChecked(),false);assert.equal(await page.locator('#ai-report-history-reference').isEnabled(),false);assert.match(await page.locator('#ai-report-read-scope').textContent(),/参考 0 条历史记录/);await page.locator('#ai-report-back').click();assert.equal(await calls(),1);
  await page.setViewportSize({width:320,height:740});await page.goto(fixture.origin+'/records?view=list');await page.locator('#record-select-page:not([disabled])').waitFor();assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));console.log('Saved report exposes exact source link; global history opt-out shown unchecked/disabled with zero records; new record-list controls fit 320px: passed.');
 }else if(mode==='layout'){
  await entry();await generate();await screenshot('AI训练复盘-窄屏.png');await page.setViewportSize({width:320,height:740});assert(await dialog.evaluate(node=>node.scrollWidth<=node.clientWidth));await page.locator('#ai-report-back').click();
  await page.setViewportSize({width:1440,height:1000});await page.goto(fixture.origin+'/records?view=list');await page.locator('#record-select-page:not([disabled])').check();await page.locator('#records-compare:not([disabled])').click();await page.getByRole('button',{name:'生成对比解读',exact:true}).click();await page.locator('#ai-report-options').waitFor({state:'visible'});await generate();assert(await page.locator('#ai-report-answer .ai-report-comparison').isVisible());assert.match(await page.locator('#ai-report-answer .ai-report-comparison').textContent(),/前次：.*后次：/s);await screenshot('复测对比.png');console.log('Final 320/390 review and 1440 comparison render exposes system observations separately from AI interpretation: passed.');
 }else if(mode==='cancel'){
  await entry();await post('/api/v1/_fixture/ai-control',{hold:true});
  await page.locator('#ai-report-consent').check();await page.locator('#ai-report-generate').click();
  await page.locator('#ai-report-stop').waitFor({state:'visible'});await page.locator('#ai-report-stop').click();
  await post('/api/v1/_fixture/ai-control',{hold:false});
  await page.locator('#ai-report-result-title').filter({hasText:'已停止'}).waitFor({timeout:8000}).catch(async error=>{console.log(JSON.stringify({rows:await reports(),error:await page.locator('#ai-report-error').textContent(),title:await page.locator('#ai-report-result-title').textContent()}));throw error;});
  assert.equal(await calls(),1);console.log('Cancellation from compact report view: passed.');
 }else{
  await page.goto(fixture.origin+'/records?view=overview');await page.locator('#overview-filters [name=action_id]').selectOption('barbell_overhead_press');await page.locator('#overview-ai-review').click();await page.locator('#ai-report-options').waitFor({state:'visible'});
  assert.equal(await calls(),0);assert.match(await page.locator('#ai-report-read-scope').textContent(),/参考 2 条历史记录/);assert.equal(await page.locator('#ai-report-consent').isChecked(),true);
  await page.locator('#ai-report-consent').uncheck();await page.locator('#ai-report-generate').click();await page.locator('#ai-report-error').filter({hasText:'请先确认'}).waitFor();assert.equal(await calls(),0);await generate();assert.equal(await calls(),1);
  await screenshot('AI训练复盘-窄屏.png');const saved=(await reports())[0];
  await page.locator('#ai-report-back').click();assert.equal(await page.locator('#overview-ai-review').evaluate(node=>node===document.activeElement),true);
  await page.reload();await page.locator('#overview-ai-review').click();await page.locator('#ai-report-dialog .report-export-history:not([open])>summary').click();await page.locator('#ai-report-history .report-history-row').first().click();await page.locator('.ai-report-summary').waitFor();assert.equal(await calls(),1);
  await page.setViewportSize({width:320,height:740});assert((await dialog.boundingBox()).width<=320);assert(await dialog.evaluate(node=>node.scrollWidth<=node.clientWidth));assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.keyboard.press('Escape');assert.equal(await dialog.isVisible(),false);
  await page.setViewportSize({width:1440,height:1000});await page.goto(fixture.origin+'/records?view=list');await page.locator('#record-select-page:not([disabled])').check();await page.locator('#records-compare:not([disabled])').click();await page.getByRole('button',{name:'生成对比解读',exact:true}).click();await page.locator('#ai-report-options').waitFor({state:'visible'});
  assert.match(await page.locator('#ai-report-scope').textContent(),/前次、后次/);assert(await page.locator('.ai-report-comparison').count()>0);await generate();assert.equal(await calls(),2);await screenshot('复测对比.png');
  await page.locator('#ai-report-delete').click();await page.locator('#ai-report-delete-yes').click();await dialog.getByText('复盘已删除，训练记录仍保留。',{exact:true}).waitFor();assert.equal(await calls(),2);assert.equal((await reports()).length,2);
  await page.locator('#ai-report-back').click();await page.setViewportSize({width:390,height:844});await entry();
  const profile=(await(await browser.request.get(fixture.origin+'/api/v1/me/training-profile')).json()).data;
  const csrf=(await(await browser.request.get(fixture.origin+'/api/v1/auth/csrf')).json()).data.csrf_token;
  const changed=await browser.request.patch(fixture.origin+'/api/v1/me/training-profile',{data:{expected_version:profile.version,experience:'beginner'},headers:{'X-CSRFToken':csrf,'Idempotency-Key':crypto.randomUUID()}});assert.equal(changed.status(),200);
  await page.locator('#ai-report-consent').check();await page.locator('#ai-report-generate').click();await page.locator('#ai-report-error').filter({hasText:'已变化'}).waitFor();assert.equal(await calls(),2);assert.equal(await page.locator('#ai-report-consent').isChecked(),true);
  await page.locator('#ai-report-refresh').click();await page.locator('#ai-report-generate:not([disabled])').waitFor();await post('/api/v1/_fixture/ai-control',{hold:true});await page.locator('#ai-report-consent').check();await page.locator('#ai-report-generate').click();await page.locator('#ai-report-stop').waitFor({state:'visible'});for(let attempt=0;attempt<100&&(await calls())<3;attempt++)await page.waitForTimeout(50);assert.equal(await calls(),3,'先确认替身已开始，才验证运行中取消');await page.locator('#ai-report-stop').click();await post('/api/v1/_fixture/ai-control',{hold:false});await page.locator('#ai-report-result-title').filter({hasText:'已停止'}).waitFor();assert.equal(await calls(),3);
  assert.equal(saved.kind,'review');console.log('Actual report APIs/tasks→overview scope/consent→saved history after reload→320/390/1440 layout→comparison/body delete→stale profile prevents charge→cancel running: passed.');
 }
 assert.equal(await page.evaluate(()=>window.cameraRequests),0);assert.deepEqual(errors,[]);
}finally{await fixture.close();}
