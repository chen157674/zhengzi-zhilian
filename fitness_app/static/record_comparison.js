import {api,keepDialogFocus} from './app.js';
import {el,dateFormat} from './record_common.js';
import {historyVideo} from './record_video.js';
import {openAIReport} from './ai_report_ui.js';

let active=null;
export async function openRecordComparison(ids,{returnTo='/records?view=list'}={}){
  if(active||ids.length!==2)return;
  const dialog=el('dialog',undefined,'record-comparison-dialog'),heading=el('div',undefined,'approved-dialog-heading'),close=el('button','×','approved-close'),content=el('div'),state=el('p','正在读取两条记录…','field-help');
  close.type='button';close.setAttribute('aria-label','关闭记录对比');heading.append(el('h2','同项动作对比'),close);content.append(state);dialog.append(heading,content);document.body.append(dialog);
  const opener=document.activeElement,players=[];let closed=false;
  dialog.showModal();const release=keepDialogFocus(dialog);active=dialog;
  function dismiss(restore=true){if(closed)return;closed=true;for(const player of players)player.dispose();release();dialog.close();dialog.remove();active=null;if(restore)opener?.focus({preventScroll:true});}
  close.onclick=()=>dismiss();dialog.addEventListener('cancel',event=>{event.preventDefault();dismiss();});
  try{
    const snapshots=await Promise.all(ids.map(id=>api('/api/v1/records/'+encodeURIComponent(id))));if(closed)return;
    snapshots.sort((a,b)=>a.started_at_ms-b.started_at_ms);const ordered=snapshots.map(x=>x.session_id);
    const result=await api('/api/v1/records/comparison',{method:'POST',body:{record_ids:ordered,expected_versions:Object.fromEntries(snapshots.map(x=>[x.session_id,x.state_version]))}});if(closed)return;
    content.replaceChildren();const columns=el('div',undefined,'record-comparison-columns');
    for(const [index,record]of snapshots.entries()){
      const section=el('section'),video=el('div',undefined,'comparison-record-media'),link=el('a','查看原记录','text-link');
      section.append(el('h3','记录 '+(index===0?'A':'B')+' · '+dateFormat.format(record.started_at_ms)),el('p',record.action_name+' · '+(record.source==='camera'?'实时分析':'视频分析'),'field-help'));
      section.append(video);
      if(record.source==='camera'){
        const empty=el('div',undefined,'source-missing');empty.append(el('h3','本次实时分析没有录像'),el('p','可以对照已保存的观察数据。','field-help'));video.append(empty);
      }else players.push(historyVideo(video,record.session_id,false,record,'comparison-'+index));
      const quantity=record.counting_available?(record.unit==='seconds'?record.hold_ms/1000+' 秒':record.total_count+(record.unit==='per_side_reps'?' 次/侧':' 次')):'未包含可用计数';
      section.append(el('p','参考完成量：'+quantity,'comparison-quantity'));
      link.href='/records/'+record.session_id+'?'+new URLSearchParams({returnTo});link.dataset.nav='';section.append(link);columns.append(section);
    }
    content.append(columns);const table=el('table',undefined,'record-comparison-table'),thead=el('thead'),head=el('tr');for(const label of ['同项观察','记录 A','记录 B'])head.append(el('th',label));thead.append(head);table.append(thead);const tbody=el('tbody');
    for(const pair of result.comparisons){
      const display=value=>pair.unit==='ms'?(value.issue/1000).toFixed(1)+' / '+(value.evaluated/1000).toFixed(1)+' 秒观察':value.issue+' / '+value.evaluated+' 次观察';
      const row=el('tr');row.append(el('td',pair.title+(pair.side==='left'?' · 左侧':pair.side==='right'?' · 右侧':'')),el('td',display(pair.before)),el('td',display(pair.after)));tbody.append(row);
    }
    if(!result.comparisons.length){const row=el('tr'),cell=el('td','暂无可比证据');cell.colSpan=3;row.append(cell);tbody.append(row);}table.append(tbody);content.append(table);
    content.append(el('p',result.reason||'对比范围：相同动作、检查项、机位与测量方式。','field-help'));
    const bar=el('div',undefined,'approved-dialog-actions'),back=el('button','返回选择','button ghost'),generate=el('button','生成对比解读','button');back.type=generate.type='button';back.onclick=()=>dismiss();generate.disabled=!result.comparable;generate.onclick=()=>{dismiss(false);openAIReport({kind:'comparison',record_ids:ordered});};bar.append(back,generate);content.append(bar);
  }catch(error){if(!closed){content.replaceChildren(el('p',error.message,'approved-dialog-error'));const retry=el('button','返回重新选择','button secondary');retry.type='button';retry.onclick=()=>dismiss();content.append(retry);}}
  dialog.addEventListener('close',()=>{if(!closed)dismiss();},{once:true});
}
