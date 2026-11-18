// 成长看板聚焦改版：隔离库，覆盖初始/计划/有数据状态，不发起 AI 请求。
import assert from 'node:assert/strict';
import path from 'node:path';
import {BrowserFixture,root} from './formal_browser_support.mjs';
const fx=new BrowserFixture('growth-focus'),errors=[];
const out=path.join(root,'第五轮优化','实现核对');
try{
 fx.seed(`import os,sys,datetime as dt
from fitness_app import create_app,db_schema as s
from fitness_app.auth import initialize_admin
from fitness_app.catalogue import get_action
from fitness_app.paths import ROOT
from sqlalchemy import select
sys.path.insert(0,str(ROOT/'tests'))
from test_training_plans import draft
app=create_app({'TESTING':True,'DATABASE_PATH':os.environ['FITNESS_DATABASE_PATH']})
db=app.extensions['database']
initialize_admin(db,'Focus_Admin','focus-fixture-password')
for name in ['Focus_Empty','Focus_Plan','Focus_Data']:
 client=app.test_client();token=client.get('/api/v1/auth/csrf').json['data']['csrf_token']
 email=name.lower()+'@gmail.com'
 r=client.post('/api/v1/auth/register',json={'email':email,'password':'focus-fixture-password','password_confirmation':'focus-fixture-password'},headers={'X-CSRFToken':token});assert r.status_code==201,r.json
 client.post('/api/v1/auth/login',json={'username':email,'password':'focus-fixture-password'},headers={'X-CSRFToken':token})
 with client.session_transaction() as state:auth=state['_user_id']
 with db.transaction(False) as c:uid=c.scalar(select(s.auth_sessions.c.user_id).where(s.auth_sessions.c.id==auth))
 token=client.get('/api/v1/auth/csrf').json['data']['csrf_token']
 if name=='Focus_Empty':continue
 plans=app.extensions['training_plans'];payload=draft(plans,'每周三练 · 全身力量')
 payload['sessions'][0]['title']='全身基础训练'
 if name=='Focus_Plan':payload['sessions']=payload['sessions'][:1]
 if name=='Focus_Plan' and plans.today().weekday()<6:payload['sessions'][0]['date']=(plans.today()+dt.timedelta(days=1)).isoformat()
 p=plans.create(uid,auth,payload,s.new_id());p=plans.command(uid,auth,p['id'],'activate',{'expected_version':0,'confirmed':True},s.new_id())
 if name=='Focus_Plan':continue
 workouts=app.extensions['training_workouts'];w=workouts.create(uid,auth,{'plan_session_id':p['sessions'][0]['id'],'session_expected_version':0},s.new_id())
 w=workouts.command(uid,auth,w['id'],{'expected_version':w['state_version'],'type':'start'},s.new_id())
 for index in (1,2):w=workouts.set(uid,auth,w['id'],w['items'][0]['item_id'],index,{'expected_version':w['state_version'],'actual_reps':10,'status':'done','source':'manual'},s.new_id())
 workouts.command(uid,auth,w['id'],{'expected_version':w['state_version'],'type':'complete','confirmed':True},s.new_id())
 for action in ['bodyweight_squat','machine_chest_press','bodyweight_squat']:
  r=client.post('/api/v1/sessions',json={'action_id':action,'capability_version':get_action(action)['capability_version'],'source':'camera','mode':'free','target':None},headers={'X-CSRFToken':token,'Idempotency-Key':s.new_id()})
  assert r.status_code==201
  sid=r.json['data']['session_id'];store=app.extensions['session_store'];store.set_state(uid,sid,'running',s.new_id());rule=get_action(action)['issues'][0]
  for i in range(1,4):
   finding={'code':rule['code'],'status':'issue' if i==1 else 'not_observed','rule_version':rule.get('rule_version'),'body_parts':rule.get('body_parts'),'evidence_level':'reference','message':rule.get('title','动作提醒'),'suggestion':rule.get('suggestion','保持稳定'),'evidence':{'view':'side'},'start_ms':(i-1)*1000,'end_ms':i*1000}
   store.append_event(uid,sid,{'event_seq':i,'set_index':1,'side':'none','start_ms':(i-1)*1000,'end_ms':i*1000,'algorithm_version':'synthetic-ui-fixture','feature_schema_version':'synthetic-ui-fixture','assessments':[finding],'active_ms':i*1000})
  store.set_state(uid,sid,'ended_early',s.new_id(),reason='fixture_end')
 journal=app.extensions['diet_journal']
 for i in range(5):journal.save(uid,auth,{'consumed_date':(journal.today()-dt.timedelta(days=i)).isoformat(),'meal_type':'breakfast','foods':[{'name':'鸡蛋','group':'protein','amount':'两个'},{'name':'燕麦','group':'staple','amount':None},{'name':'苹果','group':'fruit','amount':None}]},s.new_id())
db.dispose()`);
 fx.start();
 const context=await fx.browser('Edge',{width:1440,height:1000});
 const page=await context.newPage();
 page.on('pageerror',error=>errors.push(error.message));
 let aiCalls=0;
 page.on('request',r=>{if(r.method()==='POST'&&/review-jobs|plan-draft-jobs/.test(r.url()))aiCalls++;});
 async function ready(){await page.locator('#review-page[data-growth-state=ready]').waitFor();}
 async function tab(name){await page.locator('[data-growth-tab='+name+']').click();assert(await page.locator('#growth-panel-'+name).isVisible());}
 for(const user of ['Focus_Empty','Focus_Plan','Focus_Data']){
  await context.clearCookies();await fx.apiLogin(context,user.toLowerCase()+'@gmail.com','focus-fixture-password');
  await page.goto(fx.origin+'/training/review');await ready();
  const populated=user==='Focus_Data';
  assert.equal(await page.locator('.growth-day').count(),7);
  assert.equal(await page.locator('#growth-training-empty').isVisible(),!populated);
  assert.equal(await page.locator('#review-summary').isVisible(),populated);
  assert.equal(await page.locator('#growth-coach-section').isVisible(),populated);
  assert.equal(await page.locator('#growth-records-section').count(),0);assert.equal(await page.locator('#growth-next-action').count(),0);assert.equal(await page.locator('#growth-calendar').count(),0);
  assert.equal(await page.locator('.growth-progress-number strong').textContent(),populated?'1':'0');
  if(user==='Focus_Plan')assert.match(await page.locator('#growth-review-title').textContent(),/0 \/ 1/);
  for(const width of [1440,1280,1024,800,768,414,390,375,320]){
   await page.setViewportSize({width,height:width<768?844:1000});
   for(const name of ['training','actions','diet']){
    await tab(name);
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),user+' '+name+' overflow '+width);
    const cta=await page.locator('.growth-review-plan-link').boundingBox();
    assert(cta.x>=0&&cta.x+cta.width<=width+1,'主操作裁切');
    const copy=await page.locator('.growth-week-copy').boundingBox();
    assert(copy.width>=90&&copy.height<180,'进度文案被挤压 '+width);
    if(populated&&name==='actions'){
     for(const id of ['growth-action-chart','growth-observation-chart']){
      assert.equal(await page.locator('#'+id+' svg').count(),1);
      const chart=await page.locator('#'+id).boundingBox(),svg=await page.locator('#'+id+' svg').boundingBox();
      assert(svg.width>chart.width*.9&&svg.height>=200,'隐藏标签切换后的图表尺寸 '+id+' '+width);
     }
    }
   }
   await tab('training');
   if((user==='Focus_Plan'||user==='Focus_Data')&&(width===1440||width===390)){
    await page.evaluate(()=>{document.activeElement?.blur();scrollTo({top:0,behavior:'instant'});});
    await page.waitForFunction(()=>scrollY===0);
    await page.screenshot({path:path.join(out,'成长聚焦-'+(populated?'有记录':'待训练')+'-'+(width===1440?'桌面':'手机')+'.jpg'),fullPage:true,quality:75,type:'jpeg',animations:'disabled'});
   }
  }
  await page.setViewportSize({width:1440,height:1000});
  await page.locator('#growth-tab-training').focus();await page.keyboard.press('ArrowRight');
  assert.equal(await page.locator('#growth-tab-actions').getAttribute('aria-selected'),'true');
  await page.keyboard.press('End');assert.equal(await page.locator('#growth-tab-diet').getAttribute('aria-selected'),'true');
  await page.locator('[data-growth-days="30"]').click();await ready();
  assert.equal(await page.locator('.growth-diet-dot').count(),30);
  assert.equal(await page.locator('#growth-tab-diet').getAttribute('aria-selected'),'true');
  assert.equal(await page.locator('#growth-activity-days .growth-day').count(),30);if(populated)assert.equal(await page.locator('.growth-composition-row').count(),2);
  else assert(await page.locator('#growth-diet-empty').isVisible());
  await page.locator('[data-growth-days="7"]').click();await ready();await tab('training');
  await page.locator('#growth-fullscreen').click();await page.waitForFunction(()=>Boolean(document.fullscreenElement));
  await page.locator('#growth-fullscreen').click();await page.waitForFunction(()=>!document.fullscreenElement);
  if(user==='Focus_Empty'){
   await page.route('**/api/v1/training/reviews/week?*',async route=>{
    const response=await route.fetch(),json=await response.json();
    json.data.retests.awaiting=1;await route.fulfill({response,json});
   });
   await page.reload();await ready();await tab('actions');
   assert(await page.locator('#growth-action-distribution').isHidden());
   assert(await page.locator('#review-retests').isVisible());
   assert.match(await page.locator('#review-retests').textContent(),/待复测 1 项/);
   await page.unroute('**/api/v1/training/reviews/week?*');
  }
 }
 await page.locator('#growth-coach-section summary').click();assert(await page.locator('#review-generate').isVisible());
 // 周切换失败必须隐藏旧数据，重试后恢复，而非把旧一周当成新一周。
 await page.route('**/api/v1/training/reviews/week?*',route=>route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:{code:'UNAVAILABLE',message:'隔离测试：暂时无法读取'}})}));
 await page.locator('#review-prev-week').click();await page.locator('#review-error').waitFor();
 assert(await page.locator('.growth-content').isHidden());
 await page.unroute('**/api/v1/training/reviews/week?*');await page.locator('#review-retry').click();await ready();
 assert.match(await page.locator('#growth-review-title').textContent(),/计划/);
 await page.locator('#review-this-week').click();await ready();
 await tab('actions');await page.locator('#growth-action-links a').first().click();
 await page.waitForURL('**/records?*');assert(new URL(page.url()).searchParams.has('date_from'));
 await page.locator('#record-rows tr[data-session-id]').first().waitFor();
 assert.equal(await page.locator('.records-head-actions .view-segmented').count(),0);
 assert.match(await page.locator('.records-page .page-heading p').textContent(),/逐次/);
 await page.goto(fx.origin+'/training/plan');await page.locator('.plan-day').first().waitFor();
 assert.equal(await page.locator('.plan-day').count(),7);
 await page.goto(fx.origin+'/records');await page.locator('#overview-filters').waitFor();
 assert.equal(await page.locator('#overview-page h1').textContent(),'动作专项分析');
 for(const width of [1440,390]){await page.setViewportSize({width,height:900});await page.locator('[data-nav-group=training] summary').click();assert.equal(await page.locator('#nav-panel-training small').count(),3);assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));await page.locator('[data-nav-group=training] summary').click();}

 // 此样式文件也供餐食页使用，核对其响应式及手动保存/删除。
 await page.goto(fx.origin+'/coach/meals');
 await page.locator('#meal-journal-page[data-ready=true]').waitFor();
 for(const width of [1440,768,390,320]){
  await page.setViewportSize({width,height:900});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'餐食页溢出 '+width);
 }
 await page.locator('[data-food-name]').fill('菠菜');
 await page.locator('[data-food-group]').selectOption('vegetable');
 await page.locator('[data-food-amount]').fill('一碗');
 await page.locator('#meal-save').click();
 await page.locator('#meal-status').filter({hasText:'这一餐已记下'}).waitFor();
 const meal=page.locator('.journal-meal').filter({hasText:'菠菜'});
 await meal.waitFor();await meal.getByRole('button',{name:'删除',exact:true}).click();
 await page.locator('[data-dialog-confirm]').click();await meal.waitFor({state:'detached'});
 assert.equal(aiCalls,0);assert.deepEqual(errors,[]);
 console.log('PASS: 三种数据状态 × 九种宽度 × 三个标签；键盘、全屏、范围、图表恢复、周切换失败/重试、记录跳转；餐食四档布局及手动保存/删除；零AI调用。');
}finally{await fx.close();}
