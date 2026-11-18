// 独立AI页面：真实本人记录与页面路由，AI协议受控，不连接供应商。
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {BrowserFixture,root} from './formal_browser_support.mjs';

const fx=new BrowserFixture('round4-coach-ui'),errors=[];
const output=path.resolve(root,process.env.FITNESS_UI_EVIDENCE_DIR||'第四轮优化/实现核对');
assert(!path.relative(root,output).startsWith('..'),'证据目录须在项目内');fs.mkdirSync(output,{recursive:true});
const layouts=[];
async function checkLayout(page,width,height){
  await page.setViewportSize({width,height});
  const layout=await page.evaluate(()=>{
    const box=selector=>{const r=document.querySelector(selector).getBoundingClientRect();return {top:r.top,bottom:r.bottom,width:r.width,height:r.height};};
    return {width:innerWidth,height:innerHeight,scrollWidth:document.documentElement.scrollWidth,
      composer:box('#ai-chat-form'),content:box('.ai-assistant-content'),input:box('#ai-chat-input'),
      overflow:getComputedStyle(document.querySelector('.ai-assistant-content')).overflowY};
  });
  assert(layout.scrollWidth<=width,`${width}横向溢出`);
  assert(layout.composer.width>240&&layout.input.width>220,`${width}输入框过窄`);
  assert(layout.content.height>70,`${width}对话区被压缩`);
  assert.equal(layout.overflow,'auto');
  if(height>=844)assert(layout.composer.bottom<=height-(width<768?68:0)+1,`${width}输入框被底部导航遮住`);
  layouts.push(layout);
}
try {
  const {sid}=JSON.parse(fx.seed(`import os,json
from sqlalchemy import select
from fitness_app import create_app,db_schema as s
from fitness_app.auth import initialize_admin
from fitness_app.catalogue import get_action
app=create_app({'TESTING':True,'DATABASE_PATH':os.environ['FITNESS_DATABASE_PATH']})
db=app.extensions['database'];initialize_admin(db,'Round4_Coach','round4-coach-password')
client=app.test_client();token=client.get('/api/v1/auth/csrf').json['data']['csrf_token']
assert client.post('/api/v1/auth/login',json={'username':'Round4_Coach','password':'round4-coach-password'},headers={'X-CSRFToken':token}).status_code==200
with client.session_transaction() as state:auth=state['_user_id']
with db.transaction(False) as c:uid=c.scalar(select(s.auth_sessions.c.user_id).where(s.auth_sessions.c.id==auth))
store=app.extensions['session_store']
sid=store.create(uid,auth,{'action_id':'standing_dumbbell_curl','capability_version':get_action('standing_dumbbell_curl')['capability_version'],'source':'camera','mode':'free','target':None},s.new_id())['session_id']
store.set_state(uid,sid,'running',s.new_id(),auth_id=auth);store.set_state(uid,sid,'ended_early',s.new_id(),auth_id=auth,reason='fixture_end')
print(json.dumps({'sid':sid}));db.dispose()`));
  fx.start();const context=await fx.browser('Edge',{width:1440,height:1000});
  await fx.apiLogin(context,'Round4_Coach','round4-coach-password');
  const page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));
  await page.addInitScript(()=>{Object.defineProperty(navigator,'clipboard',{value:{writeText:async text=>{window.copiedCoachReply=text;}}});});
  const cid='11111111-1111-4111-8111-111111111111',rid='22222222-2222-4222-8222-222222222222';
  let created=0,sent=0,phase='running';
  const answer={summary:'先放慢回程，关注上臂是否稳定。',
    explanations:[{evidence:{title:'先观察动作节奏'},explanation:'下一组可以先用熟悉的重量，把注意力放在回程是否平稳、上臂是否跟着移动。',adjustment:'每组只关注一个调整点，结束后再回看记录。'}],
    next_focus:[{text:'先完成一组，记录动作感受。'},{text:'对照本次反馈，再决定是否调整重量。'}],limitations:[]};
  await page.route('**/api/v1/ai/conversations',route=>{
    const body=route.request().postDataJSON();assert.equal(body.context_kind,'record');assert.equal(body.context_id,sid);created++;
    return route.fulfill({status:201,json:{data:{conversation_id:cid,context_revision:'fixture',context_current:true}}});
  });
  await page.route('**/api/v1/ai/conversations/*',route=>route.fulfill({json:{data:{closed:true}}}));
  await page.route('**/api/v1/ai/conversations/*/messages',route=>{
    const body=route.request().postDataJSON();assert.equal(body.consent,true);assert.equal(body.include_current_frame,undefined);
    sent++;phase=sent===1?'running':'succeeded';
    return route.fulfill({status:202,json:{data:{request_id:rid,status:'queued'}}});
  });
  await page.route('**/api/v1/ai/requests/*',route=>route.fulfill({json:{data:{
    status:phase,context_current:true,state_version:1,
    ...(phase==='succeeded'?{result:answer}:{stream_text:'正在结合所选记录观察。'})
  }}}));
  await page.route('**/api/v1/ai/requests/*/cancel',route=>{
    phase='cancelled';return route.fulfill({status:202,json:{data:{request_id:rid,status:'cancelled'}}});
  });
  await page.goto(fx.origin+'/coach?record='+sid);
  assert.equal(await page.locator('.coach-main').getAttribute('data-record-id'),sid);
  assert.equal(await page.locator('#coach-context-record').inputValue(),sid);
  assert.equal(await page.locator('#ai-assistant').getAttribute('aria-modal'),null);
  assert.equal(created,0,'进入页面不应创建会话');
  assert.equal(await page.locator('.coach-tools a').count(),3);
  for(const width of [320,375,390,430,768,1024,1440]){
    await checkLayout(page,width,width<768?844:1000);
  }
  await page.setViewportSize({width:1440,height:1000});
  await page.screenshot({path:path.join(output,'AI教练-对话首页-桌面.png'),fullPage:true,animations:'disabled'});
  await page.setViewportSize({width:390,height:844});
  await page.screenshot({path:path.join(output,'AI教练-对话首页-手机.png'),fullPage:true,animations:'disabled'});
  for(const button of await page.locator('[data-ai-prompt]').all()){
    await button.click();
    assert.equal(await page.locator('#ai-chat-input').inputValue(),await button.getAttribute('data-ai-prompt'));
    assert.equal(created,0);assert.equal(sent,0,'快捷问题只能填入，不得自动发送');
  }
  await page.locator('[data-ai-new]').click();await page.locator('#confirm-dialog').waitFor();
  await page.locator('[data-dialog-cancel]').click();
  assert.match(await page.locator('#ai-chat-input').inputValue(),/选择的训练记录/);
  assert.equal(created,0,'取消新对话必须保留草稿且不创建会话');
  await page.locator('#coach-nutrition-entry').click();await page.locator('#confirm-dialog').waitFor();
  await page.locator('[data-dialog-cancel]').click();
  assert.match(page.url(),/\/coach\?record=/,'饮食入口须遵守离开保护');
  await page.setViewportSize({width:1440,height:1000});
  await page.locator('#ai-chat-input').fill('这条记录先关注什么？');
  await page.locator('#ai-chat-input').press('Enter');
  await page.locator('.ai-chat-message[data-streaming=true]').filter({hasText:'正在结合所选记录'}).waitFor();
  await page.locator('.coach-context>a').click();
  assert.match(page.url(),/\/coach\?record=/,'生成中应先停止或核对请求');
  await page.locator('#ai-chat-stop').click();await page.locator('#ai-chat-stop').waitFor({state:'hidden'});
  await page.locator('[data-ai-new]').click();await page.locator('#ai-chat-welcome').waitFor();
  await page.locator('#ai-chat-input').fill('下一组怎么观察？');await page.locator('#ai-chat-input').press('Enter');
  await page.locator('.ai-copy-reply').waitFor();
  assert.equal(sent,2);assert.equal(created,2);
  await page.locator('.ai-copy-reply').click();
  assert.match(await page.evaluate(()=>window.copiedCoachReply),/先放慢回程/);
  await page.screenshot({path:path.join(output,'AI教练-记录上下文.png'),fullPage:true,animations:'disabled'});
  for(const width of [320,375,390,430,768,1024,1440]){
    await checkLayout(page,width,width<768?844:1000);
  }
  await page.setViewportSize({width:390,height:844});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  assert.equal(await page.locator('main').evaluate(node=>node.inert),false);
  const scroll=await page.locator('.ai-assistant-content').evaluate(node=>{
    const original=node.scrollTop;node.scrollTop=0;const top=node.scrollTop;node.scrollTop=node.scrollHeight;return {original,top,bottom:node.scrollTop,height:node.clientHeight,total:node.scrollHeight};
  });
  assert(scroll.total>scroll.height&&scroll.bottom>scroll.top,'长回复应在对话区独立滚动');
  await page.locator('.ai-assistant-content').evaluate(node=>{node.scrollTop=0;});
  await page.screenshot({path:path.join(output,'AI教练-记录上下文-窄屏.png'),fullPage:true,animations:'disabled'});
  await page.locator('#ai-chat-input').fill('尚未发送的内容');
  await page.locator('.coach-context>a').click();
  await page.locator('#confirm-dialog').waitFor();
  await page.locator('[data-dialog-cancel]').click();
  assert.equal(await page.locator('#ai-chat-input').inputValue(),'尚未发送的内容');
  await page.locator('#ai-chat-input').fill('');
  await checkLayout(page,390,568);
  await page.locator('#ai-chat-input').fill('缩高窗口中的草稿');await page.locator('#ai-chat-input').focus();
  await page.locator('#ai-chat-input').scrollIntoViewIfNeeded();
  assert.equal(await page.locator('#ai-chat-input').inputValue(),'缩高窗口中的草稿');
  await page.locator('#ai-chat-input').fill('');
  await page.setViewportSize({width:390,height:844});
  await page.locator('#coach-context-record').selectOption('');
  await Promise.all([page.waitForURL(/\/coach$/),page.locator('#coach-context-form button').click()]);
  assert.equal(await page.locator('.coach-main').getAttribute('data-record-id'),'');
  assert.equal(created,2,'切换一般上下文不应自动生成或创建会话');
  assert.deepEqual(errors,[]);
  fs.writeFileSync(path.join(output,'AI教练页面核对.json'),JSON.stringify({checked_at:new Date().toISOString(),provider:'playwright-protocol-substitute',created,sent,layouts,errors,
    checks:['快捷问题只填入','新对话草稿取消保护','饮食入口草稿保护','记录切换','Enter发送','流式回复','停止','复制','七档宽度','缩高保留输入','无自动创建会话']},null,2)+'\n');
  console.log('独立AI页七档布局、快捷问题、草稿保护、记录切换、流式/停止/新对话和复制通过；供应商协议替身。');
} finally {
  await fx.close();
}
