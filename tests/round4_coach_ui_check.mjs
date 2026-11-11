// 独立AI页面：真实本人记录与页面路由，AI协议受控，不连接供应商。
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {BrowserFixture,root} from './formal_browser_support.mjs';

const fx=new BrowserFixture('round4-coach-ui'),errors=[];
const output=path.resolve(root,process.env.FITNESS_UI_EVIDENCE_DIR||'第四轮优化/实现核对');
assert(!path.relative(root,output).startsWith('..'),'证据目录须在项目内');fs.mkdirSync(output,{recursive:true});
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
  const cid='11111111-1111-4111-8111-111111111111',rid='22222222-2222-4222-8222-222222222222';
  let created=0,sent=0,phase='running';
  const answer={summary:'先放慢回程，关注上臂是否稳定。',explanations:[],next_focus:[],limitations:[]};
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
  await page.screenshot({path:path.join(output,'AI教练-记录上下文.png'),fullPage:true,animations:'disabled'});
  await page.setViewportSize({width:390,height:844});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  assert.equal(await page.locator('main').evaluate(node=>node.inert),false);
  await page.screenshot({path:path.join(output,'AI教练-记录上下文-窄屏.png'),fullPage:true,animations:'disabled'});
  await page.locator('#ai-chat-input').fill('尚未发送的内容');
  await page.locator('.coach-context>a').click();
  await page.locator('#confirm-dialog').waitFor();
  await page.locator('[data-dialog-cancel]').click();
  assert.equal(await page.locator('#ai-chat-input').inputValue(),'尚未发送的内容');
  await page.locator('#ai-chat-input').fill('');
  await page.locator('#coach-context-record').selectOption('');
  await Promise.all([page.waitForURL(/\/coach$/),page.locator('#coach-context-form button').click()]);
  assert.equal(await page.locator('.coach-main').getAttribute('data-record-id'),'');
  assert.equal(created,2,'切换一般上下文不应自动生成或创建会话');
  assert.deepEqual(errors,[]);
  console.log('独立AI页本人上下文、流式/停止/新对话、离开保护、窄屏和无自动生成通过；供应商协议替身。');
} finally {
  await fx.close();
}
