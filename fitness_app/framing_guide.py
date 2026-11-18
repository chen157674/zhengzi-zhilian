"""取景准备与判项适用性；取景问题不是姿态错误，不自动追问动作类别。"""
from .catalogue import get_action
from .posture_rules import rules_for
from .frame_pipeline import REQUIRED

VIEWS={'front':'正面','side':'侧面','oblique':'斜侧面','unknown':'暂未确认'}
JOINTS={11:'肩',12:'肩',13:'肘',14:'肘',15:'腕',16:'腕',23:'髋',24:'髋',25:'膝',26:'膝',27:'踝',28:'踝',29:'脚部',30:'脚部',31:'脚部',32:'脚部'}


def guide(frame=None,action_id=None,requested_view='auto',fresh=True):
    frame=frame or {};quality=frame.get('framing') or {};recognition=frame.get('recognition') or {}
    action_id=action_id or (frame.get('automatic_analysis') or {}).get('action_id') or recognition.get('action_id')
    definition=get_action(action_id) if action_id else None;rules=rules_for(action_id) if definition else []
    posture=(frame.get('analysis') or {}).get('posture') or {};current=posture.get('view')
    result={'schema_version':1,'action_id':action_id,'state':'preparing','message':'单人清楚入镜，观察到动作后自动分析。',
        'steps':['尽量让当前动作所需的肩、肘、腕、髋和下肢清楚可见。'],'missing_parts':[],
        'estimated_view':current,'view_basis':posture.get('view_basis'),'requested_view':requested_view,
        'requested_view_source':'automatic' if requested_view=='auto' else 'user_provided',
        'view_is_estimate':True,'guide_is_posture_error':False,'action_clarification_required':False,
        'items':[{'code':r['code'],'title':r['title'],'views':r['views'],'body_parts':r['body_parts']} for r in rules]}
    if definition:result['steps']=[definition['view']]
    if not fresh:return {**result,'state':'waiting_for_input','message':'等待新的画面，再检查取景。','steps':['恢复输入后重新确认身体部位，旧骨架不作为当前证据。']}
    if not frame:return result
    if recognition.get('reason')=='stale_frame':return {**result,'state':'waiting_for_input','message':'等待新的画面，再检查取景。','steps':['恢复输入后重新确认身体部位，旧骨架不作为当前证据。']}
    if quality.get('person_count')==0:return {**result,'state':'no_person','message':'未检测到训练者。','steps':['检查镜头朝向，移到画面中央并保持光线清楚。']}
    if quality.get('person_count',1)>1 or quality.get('code')=='TARGET_AMBIGUOUS':return {**result,'state':'multiple_people','message':'当前训练目标不唯一。','steps':['让本次训练者单独、连续入镜，避免旁人或镜面影响。']}
    if definition:
        groups=REQUIRED[action_id]
        if quality.get('posture_scope')=='upper_body':groups=[[11,12,13,14,15,16,23,24]]
        points=quality.get('landmarks') or []
        missing=min(([i for i in group if i>=len(points) or points[i].get('valid') is not True] for group in groups),key=len)
        result['missing_parts']=list(dict.fromkeys(JOINTS.get(i,'相关部位') for i in missing))
        result['steps']=[definition['view']]
    if not quality.get('usable',False):
        result.update(state='missing_parts',message=('请让'+ '、'.join(result['missing_parts'])+'清楚入镜。') if result['missing_parts'] else '请调整取景，让关键部位持续入镜。')
        return result
    if recognition.get('reason')=='motion_unconfirmed':return {**result,'state':'waiting_for_motion','message':'等待自然动作变化，准备状态不作技术评价。'}
    if not definition:return result
    limited=[r for r in rules if current and current not in r['views']]
    if posture.get('view_basis')=='view_mismatch':return {**result,'state':'view_mismatch','message':'机位设置与画面估计不一致。','steps':['请调整拍摄位置，或选择自动判断机位。']}
    if limited:
        item=limited[0];needed='或'.join(VIEWS[v] for v in item['views'])
        result.update(state='view_limited',message=item['title']+'需要'+needed+'观察。',
            steps=['当前视角仅为自动估计；这项暂不判断对错。','若已按建议摆放相机，可在设备设置主动确认拍摄机位。'])
    else:result.update(state='visible',message='所需关键部位已检出；只评估支持的姿态项目。')
    return result
