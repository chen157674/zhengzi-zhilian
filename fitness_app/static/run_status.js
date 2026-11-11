// 用户页只呈现任务状态；用量和工具日志仍由服务端保留，不注入产品正文。
export function renderRunStatus(host,row){
 if(!host)return;
 host.querySelector(':scope > .run-details')?.remove();
 host.dataset.runState=row.status||'';
 // 状态文案和停止入口由调用页管理，避免取消后残留aria-busy。
}
