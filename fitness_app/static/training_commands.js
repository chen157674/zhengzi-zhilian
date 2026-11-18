// 持久训练写入只保留最小请求身份用于刷新后核对；不持久存草稿/身体资料/正文。
import {api,confirmDialog} from './app.js';
export function trainingCommands(scope){
 const storageKey='fitness-training-pending:'+document.body.dataset.userId+':'+scope;
 let pending=null,recovered=null,storageError=false;
 try{const saved=sessionStorage.getItem(storageKey);if(saved){recovered=JSON.parse(saved);if(typeof recovered.id!=='string'||typeof recovered.path!=='string'||!recovered.path.startsWith('/api/v1/training/'))throw new Error('原请求身份无效');}}catch{storageError=true;}
 function remember(command){try{sessionStorage.setItem(storageKey,JSON.stringify({id:command.id,path:command.path,method:command.method}));}catch{throw new Error('请启用浏览器会话存储后提交。');}}
 function forget(){try{sessionStorage.removeItem(storageKey);}catch{throw new Error('操作已提交，但浏览器未清除原请求身份；请核对原回执后继续。');}pending=recovered=null;}
 return {
  get pending(){return pending||recovered;},
  async send(path,method,body){
   if(storageError)throw new Error('请启用会话存储并重新读取训练列表。');
   if(recovered&&!pending)throw new Error('上次操作结果待核对，请先重新读取原请求。');
   if(pending&&(pending.path!==path||JSON.stringify(pending.body)!==JSON.stringify(body)))throw new Error('原操作尚待核对，请先重试原请求。');
   if(!pending){pending={id:crypto.randomUUID(),path,method,body};remember(pending);}
   const own=pending;
   try{const result=await api(path,{method,commandId:own.id,body:own.body});forget();return result;}
   catch(error){if(!error.unknown)forget();throw error;}
  },
  async reconcile(){
   if(storageError)throw new Error('请启用会话存储后新建训练。');
   const marker=pending||recovered;if(!marker)return null;
   try{const result=await api('/api/v1/training/commands/'+marker.id);if(result.resource_type==='training.abandoned'){forget();return {...result,status:'abandoned'};}if(result.status!=='succeeded')throw new Error('请稍后查看原操作状态。');forget();return result;}
   catch(error){if(error.status!==404)throw error;if(pending){const own=pending;const result=await api(own.path,{method:own.method,commandId:own.id,body:own.body});forget();return {resource_type:own.path==='/api/v1/training/plan-drafts'||own.path==='/api/v1/training/plan-drafts/from-job'?'training.plan.create':'training.reconciled',resource_id:result.id||result.session?.id||result.deleted_plan_id,result};}
    if(!await confirmDialog({title:'待提交',message:'请核对当前训练，确认后可放弃本次提交。',confirmLabel:'封存旧命令并恢复编辑'}))throw new Error('已保留原提交身份，可稍后继续核对。');
    const result=await api('/api/v1/training/commands/'+marker.id+'/abandon',{method:'POST',commandId:marker.id,body:{confirmed:true}});forget();return result;}
  }
 };
}
