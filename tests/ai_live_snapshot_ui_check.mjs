// 只验证新增的冻结时刻文案/旧片段标记；协议替身不冒充真实付费AI或实机相机。
import assert from 'node:assert/strict';
import path from 'node:path';
import {BrowserFixture,root} from './formal_browser_support.mjs';
const fixture=new BrowserFixture('round3-ai-live-ui'),errors=[];
try{
 fixture.seed("from fitness_app.paths import configure_environment;configure_environment();import os;from fitness_app.database import Database;from fitness_app.auth import initialize_admin;db=Database(os.environ['FITNESS_DATABASE_PATH']);db.initialize();initialize_admin(db,'Live_AI_Admin','Live-AI-fixture-2026');db.dispose()");
 fixture.start();const browser=await fixture.browser('Edge',{width:390,height:844});await fixture.apiLogin(browser,'Live_AI_Admin','Live-AI-fixture-2026');
 const page=await browser.newPage();page.on('pageerror',error=>errors.push(String(error)));let messages=0,opened=0,closed=0;
 await page.addInitScript(()=>{window.cameraRequests=0;if(navigator.mediaDevices)navigator.mediaDevices.getUserMedia=async()=>{window.cameraRequests++;throw new Error('Physical camera is not part of this check');};});
 await page.route('**/api/v1/ai/conversations',route=>{opened++;return route.fulfill({status:201,json:{data:{conversation_id:'11111111-1111-4111-8111-111111111111',context_revision:'fixture',context_current:true,read_scope:{live_snapshot:true,live_action_name:'杠铃肩推',selected_records:0,history_records:0}}}});});
 await page.route('**/api/v1/ai/conversations/*',route=>{assert.equal(route.request().method(),'DELETE');closed++;return route.fulfill({json:{data:{closed:true}}});});
 await page.route('**/api/v1/ai/conversations/*/messages',route=>{messages++;return route.fulfill({status:202,json:{data:{request_id:'22222222-2222-4222-8222-222222222222',status:'queued'}}});});
 await page.route('**/api/v1/ai/requests/*',route=>route.fulfill({json:{data:{status:'succeeded',context_current:true,live_context:{snapshot_only:true,matches_current_segment:false},result:{summary:'根据提问时的提示，缓慢调整姿态。',explanations:[],next_focus:[],limitations:['这里只解释选定时刻。']}}}}));
 await page.goto(fixture.origin+'/realtime');
 await page.locator('#live-workspace').evaluate(node=>{node.dataset.previewId='33333333-3333-4333-8333-333333333333';node.dataset.aiSegment='original-segment';});
 await page.locator('[data-ai-open]').first().click();await page.locator('#ai-chat-welcome').waitFor();assert.equal(await page.locator('#ai-assistant-scope').count(),0);
 await page.locator('#ai-send-consent').check();await page.locator('#ai-chat-input').fill('刚才提示该怎么调整？');await page.locator('#ai-chat-send').click();
 await page.locator('#ai-request-state').filter({hasText:'训练已变化，可更新摘要'}).waitFor();
 assert(await page.locator('#ai-assistant-refresh').isVisible());assert(await page.locator('.ai-chat-message.assistant').isVisible());assert.equal(messages,1);
 assert.equal(await page.evaluate(()=>window.cameraRequests),0);assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await page.screenshot({path:path.join(root,'data/verification/round3/ai-live-snapshot-mobile.png')});
 await page.setViewportSize({width:320,height:740});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 for(let i=0;i<6;i++){await page.locator('#ai-assistant-refresh').click();await page.locator('#ai-assistant-refresh:not([disabled])').waitFor();}
 assert.equal(opened,7);assert.equal(closed,6);assert.equal(messages,1);assert(await page.locator('#ai-chat-welcome').isVisible());
 await page.locator('#ai-assistant-close').click();assert(!(await page.locator('#ai-assistant').isVisible()));assert.deepEqual(errors,[]);
 console.log('Frozen live answer labeled; same-segment refresh closes old memory conversation six times; 320/390 layout; no camera/provider call: passed.');
}finally{await fixture.close();}
