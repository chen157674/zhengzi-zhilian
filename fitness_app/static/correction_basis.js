import {el} from './record_common.js';
// 详细动作方法直接链接到知识页，不在结果里叠加原始规则与解释入口。
export function correctionBasis(record){
 const link=el('a','动作要点 →','text-link correction-method');
 link.href='/actions/'+encodeURIComponent(record.action_id);link.dataset.nav='';return link;
}
