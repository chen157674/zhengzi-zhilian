import assert from 'node:assert/strict';
import path from 'node:path';
import fs from 'node:fs';
import {BrowserFixture,root} from './formal_browser_support.mjs';
const fixture=new BrowserFixture('round3-video-ui'),errors=[];
const mode=process.env.FITNESS_VIDEO_UI_SCOPE||'full';
try{
  const input=path.join(fixture.temporary,'source.mp4');
  fixture.seed("import sys;from pathlib import Path;sys.path.insert(0,'tests');from test_formal_media import sample_mp4;Path(__import__('os').environ['FITNESS_DATABASE_PATH']).with_name('source.mp4').write_bytes(sample_mp4(frames=100))");
  await fixture.startTestService('tests/formal_roundthree_video_service.py');
  const browser=await fixture.browser('Edge',{width:1440,height:980});await fixture.apiLogin(browser,'Upload_Admin','Round3-upload-fixture-2026');
  const page=await browser.newPage();page.on('pageerror',error=>errors.push(String(error)));
  const stats=async()=>(await(await browser.request.get(fixture.origin+'/api/v1/_fixture/video-stats')).json()).data;
  const control=async(value,unknown=false)=>{const token=(await(await browser.request.get(fixture.origin+'/api/v1/auth/csrf')).json()).data.csrf_token;assert.equal((await browser.request.post(fixture.origin+'/api/v1/_fixture/video-control',{data:{terminal_fault:value,unknown},headers:{'X-CSRFToken':token}})).status(),200);};
  if(mode==='finalize')await control(true);
  if(mode==='unknown')await control(false,true);
  await page.goto(fixture.origin+'/video');assert.equal(await page.locator('#video-action').inputValue(),'');
  await page.locator('#video-file').setInputFiles(input);await page.locator('#video-start:not([disabled])').waitFor();
  if(mode==='view'){await page.getByText('更多取景设置',{exact:true}).click();await page.locator('#video-view').selectOption('front');}
  if(mode==='skeleton')await page.locator('#video-save-skeleton').check();
  await page.locator('#video-start').click();await page.waitForURL('**/video/analyses/*');
  await page.locator('#video-auto-page[data-stage=result]').waitFor({timeout:20000});
  await page.locator('.video-segment-card').nth(mode==='unknown'?0:1).waitFor();assert.equal(await page.locator('.video-segment-card').count(),mode==='unknown'?1:2);
  const pid=page.url().split('/').at(-1),getParent=async()=>(await(await browser.request.get(fixture.origin+'/api/v1/video-analyses/'+pid)).json()).data;
  const parent=await getParent(),first=parent.segments[0],neighbor=parent.segments[1]?.current_revision?.session_id;
  await page.waitForFunction(()=>document.querySelector('#video-correction')&&!document.querySelector('#video-correction').hidden);
  assert(await page.locator('#video-correction [data-cue-title]').textContent());
  let original=await stats();assert.equal(original.pose_passes,1);assert.equal(original.pose_calls,250);
  if(mode.startsWith('delete')){
    let revisionCount=2,deletedRecords=parent.segments.map(s=>s.current_revision.session_id);
    if(mode==='delete-history-cleanup'){
      const token=(await(await browser.request.get(fixture.origin+'/api/v1/auth/csrf')).json()).data.csrf_token;
      const upload=await browser.request.post(fixture.origin+'/api/v1/inputs',{multipart:{file:{name:'source.mp4',mimeType:'video/mp4',buffer:fs.readFileSync(input)}},headers:{'X-CSRFToken':token,'Idempotency-Key':crypto.randomUUID()}});assert.equal(upload.status(),202);
      const iid=(await upload.json()).data.input_id;for(let i=0;i<100;i++){const input=(await(await browser.request.get(fixture.origin+'/api/v1/inputs/'+iid)).json()).data;if(input.status==='ready')break;await page.waitForTimeout(50);}
      const response=await browser.request.post(fixture.origin+'/api/v1/video-segments/'+first.id+'/revisions',{data:{input_id:iid,action_id:'dumbbell_shoulder_press',expected_version:first.state_version},headers:{'X-CSRFToken':token,'Idempotency-Key':crypto.randomUUID()}});assert.equal(response.status(),202);
      const rid=(await response.json()).data.revision_id;for(let i=0;i<100;i++){const row=(await(await browser.request.get(fixture.origin+'/api/v1/video-revisions/'+rid)).json()).data;if(row.status==='completed'&&!row.processing)break;await page.waitForTimeout(60);}
      fixture.seed("import os,sys,json;from sqlalchemy import update;from fitness_app.database import Database;from fitness_app import db_schema as s;v=json.load(sys.stdin);db=Database(os.environ['FITNESS_DATABASE_PATH']);c=db.engine.connect();c.exec_driver_sql('BEGIN IMMEDIATE');c.execute(update(s.video_revisions).where(s.video_revisions.c.id==v['id']).values(cleanup_pending=1));c.commit();c.close();db.dispose()",{id:first.current_revision.id});
      await page.reload();await page.locator('.video-segment-card').nth(1).waitFor();assert.equal(await page.locator('#video-delete').isDisabled(),true);
      await page.getByRole('button',{name:'清理第1版临时副本',exact:true}).click();await page.locator('#video-delete:not([disabled])').waitFor();
      original=await stats();const updated=await getParent();revisionCount=3;deletedRecords=updated.segments.flatMap(s=>s.revisions.map(r=>r.session_id));assert.equal(updated.segments[0].revisions[0].cleanup_pending,false);
    }
    await page.locator('#video-delete:not([disabled])').waitFor();let attempts=0;const keys=[];
    let releaseRead,readWaiting=false;const readGate=new Promise(resolve=>releaseRead=resolve);
    page.once('close',()=>releaseRead());
    await page.route('**/api/v1/video-analyses/'+pid,async route=>{if(route.request().method()!=='DELETE'){if(mode==='delete-read'&&route.request().method()==='GET'){const reply=await route.fetch();readWaiting=true;await readGate;await route.fulfill({response:reply});}else await route.continue();return;}keys.push(route.request().headers()['idempotency-key']);attempts++;if(attempts===1&&['delete-pending','delete-missing'].includes(mode)){if(mode==='delete-pending')await route.fetch();await route.fulfill({status:503,json:{error:{message:'工程替身：删除回执未确认'}}});}else await route.continue();});
    if(mode==='delete-read'){
      await page.locator('#video-status-retry').evaluate(button=>button.click());while(!readWaiting)await page.waitForTimeout(25);
      assert.equal(await page.locator('#video-delete').isDisabled(),true);assert.equal(attempts,0);releaseRead();await page.locator('#video-delete:not([disabled])').waitFor();
    }
    const originalBytes=fs.readFileSync(input);
    await page.locator('#video-delete').click();await page.locator('#confirm-dialog').waitFor({state:'visible'});assert.match(await page.locator('#confirm-message').textContent(),new RegExp('2 个片段、'+revisionCount+' 次修订和 '+revisionCount+' 条关联记录'));
    if(mode==='delete'){
      await page.locator('[data-dialog-cancel]').click();assert.equal(attempts,0);assert.equal((await getParent()).analysis_id,pid);
      await page.setViewportSize({width:390,height:844});await page.locator('#video-delete').click();await page.locator('#confirm-dialog').waitFor({state:'visible'});await page.screenshot({path:path.join(root,'第四轮优化/实现核对/视频删除确认-窄屏.png'),animations:'disabled'});
      await page.setViewportSize({width:320,height:740});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    }
    if(mode==='delete-storage')await page.evaluate(()=>{const set=Storage.prototype.setItem;Storage.prototype.setItem=function(key,value){if(key.startsWith('fitness-video-delete-pending:'))throw new DOMException('fixture delete storage blocked','QuotaExceededError');return set.call(this,key,value);};});
    if(mode==='delete-version'){
      const token=(await(await browser.request.get(fixture.origin+'/api/v1/auth/csrf')).json()).data.csrf_token;
      const changed=await browser.request.put(fixture.origin+'/api/v1/records/'+first.current_revision.session_id+'/training-note',{data:{expected_version:0,note:'工程测试：确认框打开后内容变化'},headers:{'X-CSRFToken':token,'Idempotency-Key':crypto.randomUUID()}});assert.equal(changed.status(),200);
    }
    await page.locator('[data-dialog-confirm]').click();
    if(mode==='delete-storage'){await page.locator('#video-error').filter({hasText:'fixture delete storage blocked'}).waitFor();assert.equal(attempts,0);assert.equal((await getParent()).analysis_id,pid);}
    else{
      if(['delete-pending','delete-missing'].includes(mode)){
        await page.locator('#video-delete-recovery').filter({hasText:'删除结果待核对'}).waitFor();assert.equal(attempts,1);await page.reload();await page.locator('#video-delete-check').waitFor({state:'visible'});await page.locator('#video-delete-check').click();
        if(mode==='delete-missing'){await page.locator('#video-delete-check').filter({hasText:'按原请求重试删除'}).waitFor();assert.equal(attempts,1);await page.locator('#video-delete-check').click();}
      }
      if(mode==='delete-version'){await page.locator('#video-error').filter({hasText:'已变化'}).waitFor();assert.equal((await getParent()).analysis_id,pid);await page.locator('#video-delete').click();await page.locator('#confirm-dialog').waitFor({state:'visible'});await page.locator('[data-dialog-confirm]').click();}
      await page.waitForURL('**/video?deleted=*');await page.locator('#video-delete-recovery').filter({hasText:'已删除本次分析及关联记录'}).waitFor();
      assert.equal((await browser.request.get(fixture.origin+'/api/v1/video-analyses/'+pid)).status(),404);
      for(const id of deletedRecords)assert.equal((await browser.request.get(fixture.origin+'/api/v1/records/'+id)).status(),404);
      if(mode==='delete'){await page.goto(fixture.origin+'/video/analyses/'+pid);await page.waitForURL('**/video?analysis-unavailable=1');await page.locator('#video-delete-recovery').filter({hasText:'当前视频分析已不存在'}).waitFor();}
      if(mode==='delete-pending')assert.equal(attempts,1);
      if(mode==='delete-missing'){assert.equal(attempts,2);assert.equal(new Set(keys).size,1);}
    }
    assert.deepEqual(fs.readFileSync(input),originalBytes);assert.deepEqual(await stats(),original);assert.deepEqual(errors,[]);
    console.log(mode+': exact scope/cancel/ownership lifecycle or original-request recovery; source file retained, no extra inference: passed.');
  }else if(mode==='skeleton'){
    await page.goto(fixture.origin+'/records/'+first.current_revision.session_id);await page.locator('#record-skeleton-replay').waitFor();assert.equal(await page.locator('#record-skeleton-replay').evaluate(node=>node.open),false);
    const toggle=page.getByRole('checkbox',{name:'显示骨骼关键点',exact:true});assert(await toggle.isDisabled());
    await page.locator('#record-source-file').setInputFiles(input);await page.locator('.record-video:not([hidden])').waitFor();await page.waitForFunction(()=>!document.querySelector('.record-video-actions input').disabled);await toggle.check();
    const overlay=page.locator('.saved-skeleton-overlay');await overlay.waitFor({state:'visible'});
    await page.waitForFunction(()=>{const c=document.querySelector('.saved-skeleton-overlay');return c&&[...c.getContext('2d').getImageData(0,0,c.width,c.height).data].some((v,i)=>i%4===3&&v>0);});
    const originalSrc=await page.locator('.record-video').getAttribute('src');
    fixture.seed("import os;from pathlib import Path;p=Path(os.environ['FITNESS_DATABASE_PATH']).with_name('source.mp4');p.with_name('other.mp4').write_bytes(p.read_bytes()+bytes([0]))");
    await page.locator('#record-source-file').setInputFiles(path.join(fixture.temporary,'other.mp4'));await page.getByText('正在匹配原文件内容…',{exact:true}).waitFor({state:'hidden'});
    assert.equal(await page.locator('.record-video').getAttribute('src'),originalSrc);assert.deepEqual(errors,[]);
    assert.equal((await stats()).predictor_calls,original.predictor_calls);console.log('Opt-in upload persists bones; overlay disabled before match, draws saved points after exact source match, mismatched file cannot replace source; no new classification: passed.');
  }else if(mode==='view'){
    assert.equal(parent.coaching_view,'front');assert.equal(first.current_revision.coaching_view,'front');
    await page.setViewportSize({width:390,height:844});const card=page.locator('.video-segment-card').first();
    await card.getByRole('button',{name:'修改本段动作',exact:true}).click();assert.equal(await card.getByRole('combobox',{name:'本段拍摄机位',exact:true}).inputValue(),'front');
    await card.getByRole('combobox',{name:'本段拍摄机位',exact:true}).selectOption('side');
    await card.getByRole('button',{name:'仅重新分析本段',exact:true}).click();
    await page.waitForFunction(()=>[...document.querySelectorAll('.video-segment-card')][0]?.textContent.includes('本段已更新')||[...document.querySelectorAll('.video-segment-card')][0]?.textContent.includes('第2版'),{timeout:20000});
    let updated;for(let i=0;i<30;i++){updated=await getParent();if(updated.segments[0].current_revision?.revision_index===2)break;await new Promise(r=>setTimeout(r,150));}
    assert.equal(updated.segments[0].current_revision.coaching_view,'side');assert.equal(updated.segments[1].current_revision.session_id,neighbor);assert.equal(updated.coaching_view,'front');
    const record=(await(await browser.request.get(fixture.origin+'/api/v1/records/'+updated.segments[0].current_revision.session_id)).json()).data;
    assert.equal(record.config.coaching_view,'side');assert.equal((await stats()).predictor_calls,original.predictor_calls);
    console.log('Whole-video view and single-segment override persist separately; automatic action kept; neighbor untouched: passed.');
  }else if(mode==='unknown'){
    assert.equal(first.current_revision,null);assert.equal(first.kind,'unknown');
    await page.goto(fixture.origin+'/video');await page.locator('#video-recent a[href="/video/analyses/'+pid+'"]').waitFor();
    await page.locator('#video-recent a[href="/video/analyses/'+pid+'"]').click();
    await page.locator('.video-segment-card h3').filter({hasText:'未确认动作'}).waitFor();
    assert.deepEqual(await stats(),original);console.log('All-unknown video retained in recent analyses; reopening does not restart inference: passed.');
  }else if(mode==='finalize'){
    await page.locator('#video-finalize-warning').waitFor();assert.equal(parent.terminal_persisted,false);
    await control(false);await page.locator('#video-finalize-retry').click();
    await page.locator('#video-finalize-warning').waitFor({state:'hidden'});assert.equal((await getParent()).terminal_persisted,true);
    assert.deepEqual(await stats(),original);console.log('Task-status write failure→visible recovery→save original state without inference: passed.');
  }else if(mode==='layout'){
    await page.setViewportSize({width:390,height:844});const card=page.locator('.video-segment-card').first();
    await card.scrollIntoViewIfNeeded();await page.evaluate(()=>document.querySelector('.video-segment-card').scrollIntoView({block:'start',behavior:'instant'}));
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    await page.screenshot({path:path.join(root,'第四轮优化/实现核对/视频分段结果-受控.png')});
    await page.setViewportSize({width:320,height:740});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    console.log('Updated phone segment cards: 390/320 layout passed.');
  }else{
  let releaseRestore;const restoreGate=new Promise(resolve=>releaseRestore=resolve);let restoreHeld=false;
  const restorePattern='**/api/v1/video-analyses/'+pid;
  page.once('close',()=>releaseRestore());
  await page.route(restorePattern,async route=>{if(route.request().method()==='GET'&&!restoreHeld){restoreHeld=true;await restoreGate;}if(!page.isClosed())await route.continue();});
  await page.locator('#video-back').click();await page.waitForURL(fixture.origin+'/video');
  await page.locator('#video-recent a[href="/video/analyses/'+pid+'"]').click();await page.waitForURL('**/video/analyses/'+pid);
  await page.locator('#video-restore-state').waitFor({state:'visible'});assert.equal(await page.locator('#video-choices').isVisible(),false);
  await page.screenshot({path:path.join(root,'第四轮优化/实现核对/视频恢复加载.png'),animations:'disabled'});
  releaseRestore();await page.locator('.video-segment-card').nth(1).waitFor();await page.unroute(restorePattern);
  assert.equal(await page.locator('#video-restore-state').isVisible(),false);
  await page.reload();await page.locator('.video-segment-card').nth(1).waitFor();assert.deepEqual(await stats(),original);
  await page.setViewportSize({width:390,height:844});const card=page.locator('.video-segment-card').first();
  await card.getByRole('button',{name:'修改本段动作',exact:true}).click();await card.getByRole('combobox',{name:'选择本段实际动作',exact:true}).selectOption('dumbbell_shoulder_press');
  const chooser=page.waitForEvent('filechooser');await card.getByRole('button',{name:'仅重新分析本段',exact:true}).click();await(await chooser).setFiles(input);
  await card.locator('h3').filter({hasText:'哑铃推肩'}).waitFor({timeout:20000});
  const next=await getParent(),current=next.segments[0].current_revision;assert.equal(next.segments[1].current_revision.session_id,neighbor);
  const after=await stats();assert.equal(after.pose_passes,2);assert.equal(after.predictor_calls,original.predictor_calls);assert.deepEqual(after.ranges.at(-1),[first.start_ms,first.end_ms]);
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await card.locator('details').evaluate(node=>node.open=true);assert.equal(await card.locator('details a').count(),2);
  await page.evaluate(async()=>{scrollTo({top:0,left:0,behavior:'instant'});await Promise.all(document.getAnimations().filter(a=>a.effect?.getTiming().iterations!==Infinity).map(a=>a.finished.catch(()=>{})));});
  await page.screenshot({path:path.join(root,'第四轮优化/实现核对/视频分段结果-受控.png')});
  await page.goto(fixture.origin+'/records/'+first.current_revision.session_id);await page.locator('#revision-notice').filter({hasText:'历史修订'}).waitFor();
  assert.equal(await page.locator('#revision-notice a').getAttribute('href'),'/records/'+current.session_id);
  await page.goto(fixture.origin+'/records/'+current.session_id+'?assistant=1');await page.locator('#ai-chat-welcome').waitFor();assert.equal(await page.locator('#ai-assistant-scope').count(),0);
  await page.locator('#ai-assistant-close').click();assert.equal(await page.locator('#ai-assistant').isVisible(),false);
  assert.deepEqual(errors,[]);console.log('Upload auto→multi segments→refresh no restart→phone single revision→old version + correct AI context: passed (controlled evidence only).');
  }
  assert.deepEqual(errors,[]);
}finally{await fixture.close();}
