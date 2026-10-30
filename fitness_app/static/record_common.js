export const terminal=new Set(['completed','ended_early','interrupted','failed']);
export const statusNames={ready:'未开始',countdown:'准备中',running:'分析中',paused:'已暂停',waiting:'等待目标',resting:'休息中',ending:'正在结束',completed:'已完成',ended_early:'已结束',interrupted:'已中断',failed:'失败'};
export const issueNames={'squat.range':'深蹲活动幅度','push_up.range':'俯卧撑活动幅度','reverse_lunge.range':'后撤弓步活动幅度','glute_bridge.range':'臀桥抬髋幅度','sit_up.range':'仰卧起坐活动幅度','plank.hip_high':'髋位偏高','plank.hip_low':'髋位偏低','jumping_jack.arm_range':'手臂展开幅度','jumping_jack.leg_range':'双腿开合幅度','high_knees.height':'抬膝高度','curl.range':'弯举活动幅度','overhead_press.elbow':'肩推肘腕对齐','barbell_row.trunk':'划船躯干角度','deadlift.extension':'硬拉顶端伸展'};
export const dateFormat=new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',dateStyle:'medium',timeStyle:'short'});
export function el(tag,text,className){const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(className)node.className=className;return node}
export function duration(ms){const seconds=Math.floor(ms/1000);return `${Math.floor(seconds/60)}:${String(seconds%60).padStart(2,'0')}`}
export function quantity(record){if(record.counting_available===false)return '—';return record.unit==='seconds'?`${(record.hold_ms/1000).toFixed(1)} 秒`:`${record.total_count} 次`}
export function recordStatus(record){return record.save_state==='save_failed'?'保存故障':statusNames[record.status]||record.status}
export function privateError(error){if(error.status!==401)return false;document.documentElement.dataset.privateHidden='1';location.replace('/login?returnTo='+encodeURIComponent(location.pathname+location.search));return true}
