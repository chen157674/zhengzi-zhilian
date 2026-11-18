// 真实隔离服务：计划、图表、自动饮食归档、记忆修改/删除；供应商为显式协议替身。
import assert from 'node:assert/strict';
import fs from 'node:fs';import path from 'node:path';
import {BrowserFixture,root} from './formal_browser_support.mjs';
const fx=new BrowserFixture('growth-dashboard'),out=path.join(root,'第五轮优化','实现核对'),errors=[],rows=[];
try{
 fx.seed(`import os,sys,datetime as dt
from fitness_app import create_app,db_schema as s
from fitness_app.auth import initialize_admin
from fitness_app.catalogue import get_action
from fitness_app.paths import ROOT
sys.path.insert(0,str(ROOT/'tests'))
from test_training_plans import draft
app=create_app({'TESTING':True,'DATABASE_PATH':os.environ['FITNESS_DATABASE_PATH']})
db=app.extensions['database'];uid=initialize_admin(db,'Growth_Admin','growth-fixture-password')['id']
client=app.test_client()
token=client.get('/api/v1/auth/csrf').json['data']['csrf_token']
client.post('/api/v1/auth/login',json={'username':'Growth_Admin','password':'growth-fixture-password'},headers={'X-CSRFToken':token})
with client.session_transaction() as state:auth=state['_user_id']
token=client.get('/api/v1/auth/csrf').json['data']['csrf_token']
settings=app.extensions['ai_settings'];settings.environment_key=None
settings.write(uid,auth,{'expected_version':0,'source':'personal','personal_key':'sk-isolated-growth-fixture-123456'},s.new_id())
plans=app.extensions['training_plans'];p=plans.create(uid,auth,draft(plans,'一周全身训练'),s.new_id())
p=plans.command(uid,auth,p['id'],'activate',{'expected_version':p['state_version'],'confirmed':True},s.new_id())
workouts=app.extensions['training_workouts'];w=workouts.create(uid,auth,{'plan_session_id':p['sessions'][0]['id'],'session_expected_version':0},s.new_id())
w=workouts.command(uid,auth,w['id'],{'expected_version':w['state_version'],'type':'start'},s.new_id())
for index in (1,2):w=workouts.set(uid,auth,w['id'],w['items'][0]['item_id'],index,{'expected_version':w['state_version'],'actual_reps':10,'status':'done','source':'manual'},s.new_id())
workouts.command(uid,auth,w['id'],{'expected_version':w['state_version'],'type':'complete','confirmed':True},s.new_id())
for action in ('bodyweight_squat','machine_chest_press','bodyweight_squat'):
 response=client.post('/api/v1/sessions',json={'action_id':action,'capability_version':get_action(action)['capability_version'],'source':'camera','mode':'free','target':None},headers={'X-CSRFToken':token,'Idempotency-Key':s.new_id()})
 assert response.status_code==201,response.json
 sid=response.json['data']['session_id'];store=app.extensions['session_store'];store.set_state(uid,sid,'running',s.new_id())
 rule=get_action(action)['issues'][0]
 for i in range(1,4):
  finding={'code':rule['code'],'status':'issue' if i==1 else 'not_observed','rule_version':rule.get('rule_version'),'body_parts':rule.get('body_parts'),'evidence_level':'reference','message':rule.get('title','动作提醒'),'suggestion':rule.get('suggestion','保持稳定'),'evidence':{'view':'side'},'start_ms':(i-1)*1000,'end_ms':i*1000}
  store.append_event(uid,sid,{'event_seq':i,'set_index':1,'side':'none','start_ms':(i-1)*1000,'end_ms':i*1000,'algorithm_version':'synthetic-ui-fixture','feature_schema_version':'synthetic-ui-fixture','assessments':[finding],'active_ms':i*1000})
 store.set_state(uid,sid,'ended_early',s.new_id(),reason='fixture_end')
journal=app.extensions['diet_journal']
for i in range(5):
 day=(journal.today()-dt.timedelta(days=i)).isoformat()
 journal.save(uid,auth,{'consumed_date':day,'meal_type':'breakfast','foods':[{'name':'鸡蛋','group':'protein','amount':'两个'},{'name':'燕麦','group':'staple','amount':None},{'name':'苹果','group':'fruit','amount':None}]},s.new_id())
journal.remember(uid,auth,s.new_id(),'我喜欢吃西兰花。我不吃牛肉。我想增肌。')
db.dispose()`);
 await fx.startTestService('tests/formal_roundfive_knowledge_service.py');const context=await fx.browser('Edge',{width:1440,height:1000});await fx.apiLogin(context,'Growth_Admin','growth-fixture-password');
 const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
 await page.goto(fx.origin+'/coach/knowledge');await page.getByRole('link',{name:'我的成长',exact:true}).click();await page.locator('#review-page[data-growth-state=ready]').waitFor();
 assert.equal(await page.locator('#page-back').getAttribute('href'),'/coach/knowledge');
 assert.equal(await page.locator('#growth-activity-days .growth-day').count(),7);
 assert.equal(await page.locator('#review-summary .review-metric').count(),3);
 assert.equal(await page.locator('#growth-training-chart svg').count(),1);
 await page.locator('[data-growth-tab=actions]').click();assert.equal(await page.locator('#growth-action-chart svg').count(),1);
 assert.equal(await page.locator('#growth-observation-chart svg').count(),1);await page.locator('[data-growth-tab=training]').click();
 await page.locator('#growth-fullscreen').click();await page.waitForFunction(()=>Boolean(document.fullscreenElement));await page.locator('#growth-fullscreen').click();await page.waitForFunction(()=>!document.fullscreenElement);
 for(const width of [1440,1280,768,414,390,375,320]){
  await page.setViewportSize({width,height:width<768?844:1000});await page.waitForTimeout(80);
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'growth overflow '+width);
  for(const id of ['growth-training-chart','growth-action-chart','growth-observation-chart']){await page.locator('[data-growth-tab='+(id==='growth-training-chart'?'training':'actions')+']').click();const box=await page.locator('#'+id).boundingBox();assert(box.x>=0&&box.x+box.width<=width+1,id+' boundary '+width);const svg=await page.locator('#'+id+' svg').boundingBox();assert(svg.width>box.width*.9&&svg.height>200,id+' actual chart not an icon '+width);}
  await page.locator('[data-growth-tab=training]').click();const tiles=await page.locator('#review-summary .review-metric').evaluateAll(nodes=>nodes.map(n=>n.getBoundingClientRect().y));if(width>=768)assert(tiles.every(y=>Math.abs(y-tiles[0])<1),'desktop metrics share one row');
  rows.push({page:'growth',width,passed:true});
  if(width===1440||width===390){await page.evaluate(()=>{document.activeElement?.blur();scrollTo(0,0);});await page.screenshot({path:path.join(out,'成长看板-'+(width===1440?'桌面':'手机')+'.png'),fullPage:true,animations:'disabled'});}
 }
 await page.locator('[data-growth-tab=actions]').click();await page.locator('#growth-action-links a').first().click();await page.locator('#record-rows tr[data-session-id]').first().waitFor();assert.equal(await page.locator('#record-period').inputValue(),'custom');assert(new URL(page.url()).searchParams.has('date_to'));assert.equal(await page.locator('#record-rows tr[data-session-id]').count(),2);
 await page.locator('#record-rows a').first().click();await page.waitForFunction(()=>document.querySelector('#page-back')?.getAttribute('href').includes('date_to'));assert((await page.locator('#page-back').getAttribute('href')).includes('date_to'));await page.locator('#page-back').click();await page.locator('#record-rows tr[data-session-id]').first().waitFor();assert.equal(await page.locator('#record-rows tr[data-session-id]').count(),2);
 await page.goto(fx.origin+'/training/review?from=guide');await page.locator('#review-page[data-growth-state=ready]').waitFor();
 await page.locator('[data-growth-days="30"]').click();await page.locator('#review-page[data-growth-state=ready]').waitFor();assert.equal(await page.locator('.growth-diet-dot').count(),30);
 await page.locator('[data-growth-days="7"]').click();await page.locator('#review-page[data-growth-state=ready]').waitFor();
 await page.goto(fx.origin+'/coach/nutrition');await page.locator('[data-nutrition-analysis]').click();await page.locator('#knowledge-query').fill('午餐吃了一碗米饭、两个鸡蛋和一杯奶茶。');await page.locator('#knowledge-search').click();await page.getByRole('link',{name:'已记入我的餐食 ↗',exact:true}).waitFor({timeout:20000});
 await page.keyboard.press('Escape');await page.goto(fx.origin+'/coach/meals');await page.locator('#meal-journal-page[data-ready=true]').waitFor();
 assert((await page.locator('#journal-records').textContent()).includes('奶茶'));assert((await page.locator('.journal-saved-advice').textContent()).includes('下一餐'));
 let lostReceipt=true;await page.route('**/api/v1/nutrition/journal',async route=>{if(route.request().method()==='POST'&&lostReceipt){lostReceipt=false;await route.fetch();await route.abort('failed');}else await route.continue();});
 await page.locator('[data-food-name]').fill('菠菜');await page.locator('[data-food-group]').selectOption('vegetable');await page.locator('[data-food-amount]').fill('一碗');await page.locator('#meal-save').click();await page.locator('#meal-save').filter({hasText:'重试保存'}).waitFor();assert(await page.locator('[data-food-name]').isDisabled());await page.locator('#meal-save').click();await page.locator('#meal-status').filter({hasText:'这一餐已记下'}).waitFor();assert(await page.locator('[data-food-name]').isEnabled());
 await page.locator('.journal-food-chips').filter({hasText:'菠菜'}).waitFor();
 const memory=page.locator('.journal-memory-card').filter({hasText:'西兰花'});await memory.getByRole('button',{name:'修改',exact:true}).click();await memory.getByRole('textbox',{name:'修改记忆'}).fill('青菜');await memory.getByRole('button',{name:'保存',exact:true}).click();await page.locator('.journal-memory-card').filter({hasText:'青菜'}).waitFor();
 await page.locator('.journal-memory-card').filter({hasText:'青菜'}).getByRole('button',{name:'删除',exact:true}).click();await page.locator('[data-dialog-confirm]').click();await memory.waitFor({state:'detached'});
 const meal=page.locator('.journal-meal').filter({hasText:'菠菜'});await meal.getByRole('button',{name:'删除',exact:true}).click();await page.locator('[data-dialog-confirm]').click();await meal.waitFor({state:'detached'});
 for(const width of [1440,768,390,320]){await page.setViewportSize({width,height:900});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'journal overflow '+width);rows.push({page:'journal',width,passed:true});if(width===1440||width===390){await page.evaluate(()=>{document.activeElement?.blur();scrollTo(0,0);});await page.screenshot({path:path.join(out,'餐食与记忆-'+(width===1440?'桌面':'手机')+'.png'),fullPage:true,animations:'disabled'});}}
 await page.goto(fx.origin+'/training/review');await page.locator('#review-page[data-growth-state=ready]').waitFor();assert.equal(await page.locator('#page-back').count(),0);
 const state=await(await context.request.get(fx.origin+'/api/v1/training/growth?days=7')).json();assert.equal(state.data.food_days.vegetable,0);assert(state.data.diet_records>=1);
 assert.deepEqual(errors,[]);
 fs.writeFileSync(path.join(out,'成长看板核对.json'),JSON.stringify({date:new Date().toISOString(),rows,errors,auto_analysis_saved:true,memory_edit_delete:true,manual_meal_save_delete:true,full_screen:true,chart_engine:'Apache ECharts 6.1.0 local SVG',cloud_calls:0,provider:'explicit_protocol_double',result:'passed'},null,2));
 console.log('PASS:成长看板7档、餐食4档、真实计划/分析统计、全屏、自动分析归档、手动保存删除、记忆修改删除。');
}finally{await fx.close();}
