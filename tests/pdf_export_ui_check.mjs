// 真实导出服务/字体/下载/记录API；训练记录为工程夹具，不开启相机或付费AI。
import assert from 'node:assert/strict';
import path from 'node:path';
import fs from 'node:fs';
import {execFileSync} from 'node:child_process';
import {BrowserFixture,root} from './formal_browser_support.mjs';
const fixture=new BrowserFixture('round3-pdf-ui'),errors=[],mode=process.env.FITNESS_PDF_UI_SCOPE||'full';
const outputRelative=process.env.FITNESS_PDF_UI_OUTPUT||'第四轮优化/实现核对';const output=path.resolve(root,outputRelative);assert(!path.relative(root,output).startsWith('..')&&!path.isAbsolute(path.relative(root,output)));fs.mkdirSync(output,{recursive:true});fixture.env.FITNESS_PDF_UI_OUTPUT=output;
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
app=create_app({'TESTING':True,'DATABASE_PATH':os.environ['FITNESS_DATABASE_PATH']});db=app.extensions['database'];initialize_admin(db,'PDF_UI_Admin','PDF-ui-fixture-2026')
c=app.test_client();token=c.get('/api/v1/auth/csrf').json['data']['csrf_token'];assert c.post('/api/v1/auth/login',json={'username':'PDF_UI_Admin','password':'PDF-ui-fixture-2026'},headers={'X-CSRFToken':token}).status_code==200
with c.session_transaction() as state:auth=state['_user_id']
with db.transaction(False) as conn:uid=conn.scalar(select(s.auth_sessions.c.user_id).where(s.auth_sessions.c.id==auth))
store=app.extensions['session_store'];fixture=PdfRenderTests();fixture.fx=SimpleNamespace(store=store,user=uid,auth=auth,finish=lambda sid:store.set_state(uid,sid,'ended_early',s.new_id(),auth_id=auth,reason='controlled_fixture_end'))
print(json.dumps({'first':fixture.record('user_selected'),'second':fixture.record('automatically_recognized')}));db.dispose()
`));
 fixture.start();const browser=await fixture.browser('Edge',{width:390,height:844});await fixture.apiLogin(browser,'PDF_UI_Admin','PDF-ui-fixture-2026');
 await browser.addInitScript(()=>{window.cameraRequests=0;if(navigator.mediaDevices)navigator.mediaDevices.getUserMedia=async()=>{window.cameraRequests++;throw new Error('No physical camera in this check');};});
 const page=await browser.newPage();page.on('pageerror',error=>errors.push(String(error)));let created=0;
 const list=async()=>(await(await browser.request.get(fixture.origin+'/api/v1/reports/exports')).json()).data.reports;
 const removeRecord=async()=>{const data=(await(await browser.request.get(fixture.origin+'/api/v1/records/'+seeded.first)).json()).data,token=(await(await browser.request.get(fixture.origin+'/api/v1/auth/csrf')).json()).data.csrf_token;const result=await browser.request.post(fixture.origin+'/api/v1/records/delete',{data:{ids:[seeded.first],expected_versions:{[seeded.first]:data.state_version}},headers:{'X-CSRFToken':token,'Idempotency-Key':crypto.randomUUID()}});assert.equal(result.status(),200);};
 if(mode==='pending')await page.route('**/api/v1/reports/exports',async route=>{if(route.request().method()==='POST'&&created++===0){await route.fetch();await route.fulfill({status:503,json:{error:{code:'FIXTURE_UNCERTAIN','message':'工程模拟：创建结果待核对'}}});}else await route.continue();});
 await page.goto(fixture.origin+'/records/'+seeded.first);await page.locator('#record-export:not([disabled])').waitFor();await page.locator('#record-export').click();const dialog=page.locator('#report-export-dialog');await dialog.getByText('已选 1 条训练记录。',{exact:true}).waitFor();assert.equal(await page.locator('#report-export-notes').isChecked(),false);
 if(mode==='layout'){await page.locator('#report-export-back').click();await page.goto(fixture.origin+'/records?view=list');await page.locator('#record-select-page:not([disabled])').waitFor();await page.locator('#record-select-page').check();await page.locator('#records-export').click();await dialog.getByText('已选 2 条训练记录。',{exact:true}).waitFor();await page.evaluate(async()=>await Promise.all(document.getAnimations().filter(a=>a.effect?.getTiming().iterations!==Infinity).map(a=>a.finished.catch(()=>{}))));await page.screenshot({path:path.join(output,'PDF导出-窄屏.png'),animations:'disabled'});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.setViewportSize({width:320,height:740});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));assert.deepEqual(errors,[]);console.log('Final PDF dialog/source list/AI scope copy at 320/390: passed.');}else{
 await page.locator('#report-export-generate').click();
 if(mode==='pending'){
  await dialog.getByRole('button',{name:'核对原请求',exact:true}).waitFor();assert.equal((await list()).length,1);await removeRecord();await page.goto(fixture.origin+'/records?view=list&reports=1');await page.locator('#report-export-generate').filter({hasText:'核对原请求'}).waitFor();await page.locator('#report-export-generate').click();await page.locator('#report-export-result-warning').filter({hasText:'来源记录已删除'}).waitFor();assert.equal((await list()).length,1);assert.deepEqual(errors,[]);console.log('Unknown POST after server commit→source delete→new page→same request check, no regeneration: passed.');
 }else{
  await page.locator('#report-export-download:not([hidden])').waitFor({timeout:20000});
  const firstDownload=page.waitForEvent('download');await page.locator('#report-export-download').click();const file=path.join(fixture.temporary,'downloaded-report.pdf');const download=await firstDownload;assert.equal(download.suggestedFilename(),'知练同行-训练观察报告.pdf');await download.saveAs(file);assert(fs.readFileSync(file).subarray(0,5).equals(Buffer.from('%PDF-')));
  const inspected=JSON.parse(fixture.seed("import os,sys,json;from pathlib import Path;sys.path.insert(0,'tests');from test_pdf_report import pdf_reader;p=Path(os.environ['FITNESS_DATABASE_PATH']).with_name('downloaded-report.pdf');r=pdf_reader(p.read_bytes());print(json.dumps({'pages':len(r.pages),'chinese':'知练同行' in ''.join(x.extract_text() for x in r.pages)}))"));assert(inspected.pages>=2&&inspected.chinese);
  execFileSync('C:/Users/chen/.cache/codex-runtimes/codex-primary-runtime/dependencies/native/poppler/Library/bin/pdftoppm.exe',
    ['-png','-scale-to','1100',file,path.join(fixture.temporary,'pdf-page')],{
      windowsHide:true,env:{...fixture.env,XDG_CACHE_HOME:path.join(fixture.temporary,'font-cache'),LOCALAPPDATA:path.join(fixture.temporary,'localappdata'),APPDATA:path.join(fixture.temporary,'appdata')}
    });
  fixture.seed(`import os,math
from pathlib import Path
from PIL import Image,ImageOps
from fitness_app.paths import ROOT
pages=sorted(Path(os.environ['FITNESS_DATABASE_PATH']).parent.glob('pdf-page-*.png'))
assert pages
sheet=Image.new('RGB',(1600,math.ceil(len(pages)/2)*1140),'#FFF9F1')
for i,p in enumerate(pages):
 with Image.open(p) as im:
  thumb=ImageOps.contain(im.convert('RGB'),(780,1100))
  sheet.paste(thumb,((i%2)*800+(800-thumb.width)//2,(i//2)*1140+20))
target=Path(os.environ['FITNESS_PDF_UI_OUTPUT'])/'训练报告-版式核对.png'
assert target.resolve().is_relative_to(ROOT.resolve())
target.parent.mkdir(parents=True,exist_ok=True)
sheet.save(target)`);
  const reportId=(await list())[0].id;const another=page.waitForEvent('download');await page.locator('#report-export-download').click();await(await another).saveAs(path.join(fixture.temporary,'repeated.pdf'));assert.equal((await list()).length,1);
  await page.locator('#report-export-reselect').click();await page.locator('#report-export-generate').waitFor({state:'visible'});await page.locator('#report-export-generate').click();await page.locator('#report-export-download:not([hidden])').waitFor();assert.equal((await list())[0].id,reportId);assert.equal((await list()).length,1);
  await page.locator('#report-export-back').click();await page.goto(fixture.origin+'/records?view=list');await page.locator('#record-select-page:not([disabled])').waitFor();await page.locator('#record-select-page').check();await page.locator('#records-export').click();await dialog.getByText('已选 2 条训练记录。',{exact:true}).waitFor();
  await page.evaluate(async()=>await Promise.all(document.getAnimations().filter(a=>a.effect?.getTiming().iterations!==Infinity).map(a=>a.finished.catch(()=>{}))));await page.screenshot({path:path.join(output,'PDF导出-窄屏.png'),animations:'disabled'});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.setViewportSize({width:320,height:740});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));assert((await dialog.boundingBox()).width<=320);
  await page.locator('#report-export-notes').check();await page.locator('#report-export-generate').click();await page.locator('#report-export-download:not([hidden])').waitFor({timeout:20000});await removeRecord();await page.locator('#report-export-download').click();await page.locator('#report-export-result-warning').filter({hasText:'来源记录已删除'}).waitFor();assert(!(await page.locator('#report-export-download').isVisible()));assert.equal(await page.evaluate(()=>window.cameraRequests),0);assert.deepEqual(errors,[]);
  console.log('Real Chinese PDF download/re-download/deduplication→batch export→320/390 dialog→source delete blocks download; no camera or AI call: passed.');
 }
 }
}finally{await fixture.close();}
