// 只展示真实任务事件与账本估算，不输出隐藏推理或猜费用。
export function renderRunStatus(host,row){
 if(!host)return;let box=host.querySelector(':scope > .run-details');
 if(!box){box=document.createElement('details');box.className='run-details';host.append(box);}
 const open=box.open;box.replaceChildren();box.open=open;const summary=document.createElement('summary');summary.textContent='查看任务过程与用量';box.append(summary);
 const names={search_knowledge:'检索适用资料',estimate_nutrition:'本地估算参考',prepare:'核对本次范围',generate:'生成本次结果'};
 for(const event of row.events||[]){const p=document.createElement('p');p.className='field-help';p.textContent=(event.label||names[event.tool]||names[event.kind]||'处理本次任务')+' · '+({running:'进行中',completed:'完成',rejected:'未采用',failed:'失败'}[event.status]||event.status);box.append(p);}
 const usage=row.usage_summary;const p=document.createElement('p');p.className='field-help';
 p.textContent=usage&&Number.isInteger(usage.estimated_micro_cny)?'本次估算费用 '+(usage.estimated_micro_cny/1000000).toFixed(6)+' 元；以实际供应商账单为准。':'费用尚待确认，不能按零费用计算。';box.append(p);
}
