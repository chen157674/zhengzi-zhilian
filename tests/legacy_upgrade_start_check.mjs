// 按登记的schema8形状准备工程库，再由真实启动入口升级；不碰正式个人库或真实AI。
import assert from 'node:assert/strict';
import {BrowserFixture} from './formal_browser_support.mjs';
const fixture=new BrowserFixture('round3-upgrade-start');
try{
 const seeded=JSON.parse(fixture.seed(`
import os,sys,json
from types import SimpleNamespace
from sqlalchemy import select,update
from fitness_app.paths import ROOT,configure_environment
configure_environment();sys.path.insert(0,str(ROOT/'tests'))
from fitness_app.app import create_app
from fitness_app.auth import initialize_admin
from fitness_app.catalogue import get_action
from fitness_app import db_schema as s
from legacy_schema_support import legacy_shape
from test_legacy_upgrade import LegacyUpgradeTests
app=create_app({'TESTING':True,'DATABASE_PATH':os.environ['FITNESS_DATABASE_PATH']});db=app.extensions['database'];initialize_admin(db,'Upgrade_Admin','Upgrade-start-fixture-2026')
client=app.test_client();csrf=client.get('/api/v1/auth/csrf').json['data']['csrf_token'];assert client.post('/api/v1/auth/login',json={'username':'Upgrade_Admin','password':'Upgrade-start-fixture-2026'},headers={'X-CSRFToken':csrf}).status_code==200
with client.session_transaction() as state:auth=state['_user_id']
with db.transaction(False) as c:uid=c.scalar(select(s.auth_sessions.c.user_id).where(s.auth_sessions.c.id==auth))
client.get('/api/v1/account/profile')
with db.transaction() as c:c.execute(update(s.user_profiles).where(s.user_profiles.c.user_id==uid).values(nickname='升级启动保留',version=7))
store=app.extensions['session_store'];sid=store.create(uid,auth,{'action_id':'bodyweight_squat','capability_version':get_action('bodyweight_squat')['capability_version'],'source':'camera','mode':'free','target':None},s.new_id())['session_id'];store.set_state(uid,sid,'running',s.new_id(),auth_id=auth)
for i in range(1,4):store.append_event(uid,sid,{'event_seq':i,'set_index':1,'side':'none','start_ms':(i-1)*1000,'end_ms':i*1000,'algorithm_version':'fixture','feature_schema_version':'fixture','assessments':[],'active_ms':i*1000},auth_id=auth)
store.set_state(uid,sid,'ended_early',s.new_id(),auth_id=auth)
app.extensions['ai_settings'].write(uid,auth,{'expected_version':0,'source':'personal','personal_key':'sk-isolated-startup-fixture-123456','history_reference':False},s.new_id())
pid=LegacyUpgradeTests().video_parent(SimpleNamespace(app=app,db=db,store=store,user=uid,auth=auth,now=[s.now_ms()]))
legacy_shape(db,8);print(json.dumps({'record_id':sid,'parent_id':pid,'legacy_version':db.inspect_identity(allow_legacy=True)['schema_version']}));db.dispose()
`));
 assert.equal(seeded.legacy_version,8);fixture.start();
 let cookie='';
 async function http(path,{method='GET',body,token}={}){
  const response=await fetch(fixture.origin+path,{method,headers:{...(cookie?{Cookie:cookie}:{}),...(body?{'Content-Type':'application/json'}:{}),...(token?{'X-CSRFToken':token}:{})},body:body?JSON.stringify(body):undefined});
  for(const value of response.headers.getSetCookie()){if(value.startsWith('session='))cookie=value.split(';',1)[0];}
  assert.equal(response.status,200,path);return (await response.json()).data;
 }
 const token=(await http('/api/v1/auth/csrf')).csrf_token;
 await http('/api/v1/auth/login',{method:'POST',token,body:{username:'Upgrade_Admin',password:'Upgrade-start-fixture-2026'}});
 const get=path=>http(path);
 assert.equal((await get('/api/v1/records/'+seeded.record_id)).total_count,3);
 assert.equal((await get('/api/v1/account/profile')).nickname,'升级启动保留');
 const settings=await get('/api/v1/me/ai-settings');assert.equal(settings.source,'personal');assert.equal(settings.personal_key_configured,true);assert.equal(settings.history_reference,false);
 const parent=await get('/api/v1/video-analyses/'+seeded.parent_id);assert.equal(parent.status,'completed');assert.equal(parent.segments[0].current_revision.status,'completed');
 assert.equal((await get('/api/v1/video-analyses/'+seeded.parent_id+'/delete-options')).record_count,1);
 assert.equal(JSON.parse(fixture.seed("import os,json;from fitness_app.database import Database;d=Database(os.environ['FITNESS_DATABASE_PATH']);print(json.dumps(d.inspect_identity()));d.dispose()")).schema_version,11);
 console.log('Real application start upgrades isolated schema8→11; HTTP login, count, nickname, encrypted-key setting and video revision/delete-options preserved; no camera/provider call: passed.');
}finally{await fixture.close();}
