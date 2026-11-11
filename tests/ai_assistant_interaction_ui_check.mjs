// 隔离浏览器检查键盘、真实增量接口消费及失败回收；不打开相机或调用供应商。
import assert from 'node:assert/strict';
import path from 'node:path';
import {BrowserFixture,root} from './formal_browser_support.mjs';

const fixture=new BrowserFixture('ai-assistant-interaction');
fixture.env.APPDATA=path.join(fixture.temporary,'roaming');
fixture.env.LOCALAPPDATA=path.join(fixture.temporary,'localappdata');
const errors=[];let posts=0,created=0,allowFinish=false,polls=0,cancelled=false,contextCurrent=true;
const conversation='11111111-1111-4111-8111-111111111111';
const answer={summary:'先稳定身体，再参考动作提示缓慢调整。',explanations:[],next_focus:[],limitations:[]};
try{
 fixture.seed("import os\nfrom fitness_app.database import Database\nfrom fitness_app.auth import initialize_admin\ndb=Database(os.environ['FITNESS_DATABASE_PATH']);db.initialize();initialize_admin(db,'Chat_UI_Admin','Chat-UI-fixture-2026');db.dispose()");
 fixture.start();const browser=await fixture.browser('Chrome',{width:1440,height:900});
 await fixture.apiLogin(browser,'Chat_UI_Admin','Chat-UI-fixture-2026');
 const page=await browser.newPage();page.on('pageerror',e=>errors.push(String(e)));
 await page.addInitScript(()=>{if(navigator.mediaDevices)navigator.mediaDevices.getUserMedia=async()=>{throw new Error('Camera is outside this check');};});
 await page.route('**/api/v1/ai/conversations',route=>{created++;return route.fulfill({status:201,json:{data:{conversation_id:conversation,context_revision:'fixture',context_current:true,read_scope:{selected_records:0,history_records:0}}}});});
 await page.route('**/api/v1/ai/conversations/*/messages',route=>{
  const body=route.request().postDataJSON();posts++;assert.equal(body.consent,true);
  if(body.text.includes('1+1'))return route.fulfill({status:422,json:{error:{code:'AI_TOPIC_OUT_OF_SCOPE',message:'我可以帮你解读动作、调整姿态和安排训练。请问我健身相关的问题。'}}});
  return route.fulfill({status:202,json:{data:{request_id:'22222222-2222-4222-8222-222222222222',status:'queued'}}});
 });
 await page.route('**/api/v1/ai/conversations/*',route=>route.fulfill({json:{data:{closed:true}}}));
 await page.route('**/api/v1/ai/requests/*',route=>{
  polls++;
  return route.fulfill({json:{data:cancelled?{status:'cancelled',context_current:true}:allowFinish?{status:'succeeded',context_current:true,result:answer}:{status:'running',context_current:contextCurrent,state_version:1,stream_text:contextCurrent?(polls===1?'先稳定身体':'先稳定身体，再参考动作提示缓慢调整。'):''}}});
 });
 await page.route('**/api/v1/ai/requests/*/cancel',route=>{cancelled=true;return route.fulfill({status:202,json:{data:{request_id:'22222222-2222-4222-8222-222222222222',status:'cancelled'}}});});
 await page.goto(fixture.origin+'/realtime');await page.locator('[data-ai-open]').first().click();
 await page.locator('#ai-chat-welcome').waitFor();assert.equal(await page.locator('#ai-assistant-scope').count(),0);
 assert.equal(await page.locator('#ai-assistant a[href="/account/ai"]').count(),0);
 assert(await page.locator('#ai-send-consent').isChecked());
 assert(await page.locator('#ai-chat-send').isDisabled());
 const input=page.locator('#ai-chat-input');await input.fill('深蹲怎么调整？');await input.press('Shift+Enter');
 assert((await input.inputValue()).endsWith('\n'));assert.equal(posts,0);
 await input.press('Enter');await page.locator('.ai-chat-message[data-streaming=true] p').filter({hasText:'先稳定身体'}).waitFor();
 assert.equal(posts,1);assert(await page.locator('#ai-chat-send').isDisabled());assert(await input.isEnabled());
 contextCurrent=false;await page.locator('#ai-request-state').filter({hasText:'训练依据已更新'}).waitFor();
 assert.equal(await page.locator('[data-streaming=true]').count(),0);contextCurrent=true;
 await input.fill('接下来该怎么做？');await input.dispatchEvent('keydown',{key:'Enter',code:'Enter',isComposing:true});assert.equal(posts,1);
 await page.locator('#ai-assistant-close').click();assert(!(await page.locator('#ai-assistant').isVisible()));
 await page.locator('[data-ai-open]').first().click();assert.equal(created,1);
 allowFinish=true;await page.locator('.ai-copy-reply').waitFor();
 assert.equal(await page.locator('.ai-chat-message.assistant').count(),1);
 assert.equal(await page.locator('[data-streaming=true]').count(),0);
 assert.equal(await input.inputValue(),'接下来该怎么做？');
 await page.evaluate(()=>Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async text=>{window.copiedReply=text;}}}));
 await page.locator('.ai-copy-reply').click();assert.equal(await page.evaluate(()=>window.copiedReply),answer.summary);
 await input.dispatchEvent('keydown',{key:'Enter',code:'Enter',isComposing:true});assert.equal(posts,1);
 assert(await page.locator('#ai-chat-send').isEnabled());
 await page.screenshot({path:path.join(root,'第四轮优化/实现核对/AI教练-侧栏.png')});
 await page.locator('#ai-chat-new').click();await page.locator('#ai-chat-welcome').waitFor();
 await input.fill('1+1等于几？');await input.press('Enter');
 await page.locator('.ai-chat-message.assistant').filter({hasText:'健身相关的问题'}).waitFor();
 assert(!(await page.locator('#ai-chat-error').isVisible()));assert.equal(posts,2);
 await page.locator('#ai-chat-new').click();await page.locator('#ai-chat-welcome').waitFor();allowFinish=false;
 await input.fill('俯卧撑该怎么调整？');await input.press('Enter');await page.locator('#ai-chat-stop').waitFor();
 await page.locator('#ai-chat-stop').click();await page.locator('#ai-request-state').filter({hasText:'已停止'}).waitFor();
 assert.equal(await page.locator('[data-streaming=true]').count(),0);
 await page.setViewportSize({width:375,height:844});
 const mobile=await page.evaluate(()=>({width:document.documentElement.scrollWidth,viewport:innerWidth,formBottom:document.querySelector('#ai-chat-form').getBoundingClientRect().bottom,height:innerHeight}));
 assert(mobile.width<=mobile.viewport);assert(mobile.formBottom<=mobile.height+1);
 await page.screenshot({path:path.join(root,'第四轮优化/实现核对/AI教练-流式窄屏.png')});
 assert.deepEqual(errors,[]);
 console.log('Enter/Shift+Enter/IME, progressive replies, preserved draft, close/reopen, new chat, scope redirect and mobile layout: passed.');
}finally{await fixture.close();}
