import {api,RequestError} from './app.js';

export async function sendVideo(path,file,key,signal){
  const csrf=(await api('/api/v1/auth/csrf')).csrf_token,form=new FormData();form.append('file',file,file.name);
  const response=await fetch(path,{method:'POST',headers:{'X-CSRFToken':csrf,...(key?{'Idempotency-Key':key}:{})},credentials:'same-origin',cache:'no-store',body:form,signal});
  const result=await response.json();if(!response.ok)throw new RequestError(result.error?.message||'视频处理失败',response.status,result.error?.fields,result.error?.code);return result.data;
}

export function localPlayer(video){
  let url=null,objectUrl=false;
  return {set(file){this.clear();url=URL.createObjectURL(file);objectUrl=true;video.src=url;video.load()},
    setRemote(value){const target=new URL(value,location.origin);if(target.origin!==location.origin||!target.pathname.startsWith('/static/demo/'))throw new Error('演示视频地址不受支持');this.clear();url=target.href;video.src=url;video.load()},
    clear(){video.pause();video.removeAttribute('src');video.load();if(url&&objectUrl)URL.revokeObjectURL(url);url=null;objectUrl=false},has(){return Boolean(url)}};
}

export function demoAttribution(container,resource){
  container.replaceChildren();if(!resource||resource.source_type==='user_supplied')return;
  const details=document.createElement('details'),summary=document.createElement('summary'),credit=document.createElement('p'),notice=document.createElement('p'),link=document.createElement('a');
  details.className='field-help';summary.textContent='演示素材来源';link.textContent=resource.source_label||'DVIDS / Barbell Bent Over Row';
  if(resource.source_type==='user_supplied')credit.append(document.createTextNode(resource.source_label||'用户提供的本机训练视频'));
  else{const url=new URL(resource.source_url);if(url.origin!=='https://www.dvidshub.net')throw new Error('演示来源链接无效');link.href=url.href;link.target='_blank';link.rel='noopener noreferrer';credit.append(link);}
  credit.append(document.createTextNode(' · '+resource.credit+' · '+resource.license));notice.textContent=resource.non_endorsement;
  details.append(summary,credit,notice);container.append(details);
}
