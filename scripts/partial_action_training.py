"""离线融合候选的部分标签损失与来源权重；不参与线上推理。"""
from collections import Counter,defaultdict
import numpy as np


def assert_source_split(training,evaluation):
    """作者组及原视频摘要都必须隔离；评估专用媒体不能进入拟合。"""
    if not training or not evaluation:raise ValueError('训练和评估输入不能为空')
    if any(r.get('partition')!='train' or r.get('evaluation_only') or r.get('exclude_from_training') for r in training):
        raise ValueError('评估或排除来源不能进入训练')
    from fitness_app.source_partitions import enforce_source_policy
    enforce_source_policy(training)
    for key in ('group','source_sha256'):
        if any(not isinstance(r.get(key),str) or not r[key] for r in training+evaluation):raise ValueError('缺少来源身份')
        if {r[key] for r in training}&{r[key] for r in evaluation}:raise ValueError('训练与评估来源重叠: '+key)


def source_weights(rows):
    """按原类别/来源组/原片等权；两种损失共用权重，不奖励重复裁剪。"""
    if not rows:raise ValueError('训练输入不能为空')
    clips=Counter();groups=defaultdict(set);originals=defaultdict(set)
    for r in rows:
        action,group,digest=r['action_id'],r['group'],r['source_sha256']
        if any(not isinstance(v,str) or not v for v in (action,group,digest)):raise ValueError('来源权重身份无效')
        groups[action].add(group);originals[action,group].add(digest);clips[action,group,digest]+=1
    weights=np.asarray([1/(len(groups[r['action_id']])*len(originals[r['action_id'],r['group']])*clips[r['action_id'],r['group'],r['source_sha256']]) for r in rows],np.float64)
    return weights*(len(rows)/weights.sum())


def allowed_mask(rows,classes,partial=True):
    classes=list(classes)
    if len(set(classes))!=len(classes) or len(classes)<2:raise ValueError('类别表无效')
    mask=np.zeros((len(rows),len(classes)),bool);lookup={v:i for i,v in enumerate(classes)}
    for i,r in enumerate(rows):
        actions=r['label_scope']['allowed_actions'] if partial else [r['control_label']]
        if not actions or len(actions)!=len(set(actions)) or any(v not in lookup for v in actions):raise ValueError('允许类别集合无效')
        mask[i,[lookup[v] for v in actions]]=True
    return mask


def partial_loss(logits,mask,weights):
    """最小化允许类别的总概率；动作族标签不被强行拆成某个握法。"""
    import torch
    if logits.ndim!=2 or mask.shape!=logits.shape or weights.shape!=(len(logits),):raise ValueError('损失维度无效')
    if mask.dtype!=torch.bool or not torch.all(mask.any(dim=1)) or not torch.isfinite(logits).all():raise ValueError('标签掩码或数值无效')
    if not torch.isfinite(weights).all() or not torch.all(weights>0):raise ValueError('训练权重无效')
    allowed=torch.logsumexp(logits.masked_fill(~mask,-torch.inf),dim=1)
    return ((torch.logsumexp(logits,dim=1)-allowed)*weights).sum()/weights.sum()


def fit_linear(matrix,mask,weights,strength=.3,max_iter=200):
    """固定正则和初始化的CPU全批优化；验证标签不进入拟合。"""
    import torch
    torch.set_num_threads(4)
    if matrix.ndim!=2 or len(matrix)!=len(mask) or not np.isfinite(matrix).all() or strength<=0:raise ValueError('拟合输入无效')
    x=torch.as_tensor(matrix,dtype=torch.float64);target=torch.as_tensor(mask,dtype=torch.bool);w=torch.as_tensor(weights,dtype=torch.float64)
    coef=torch.zeros((mask.shape[1],matrix.shape[1]),dtype=torch.float64,requires_grad=True)
    bias=torch.zeros(mask.shape[1],dtype=torch.float64,requires_grad=True)
    optimizer=torch.optim.LBFGS([coef,bias],lr=1.,max_iter=max_iter,tolerance_grad=1e-7,tolerance_change=1e-10,line_search_fn='strong_wolfe')
    calls=0
    def closure():
        nonlocal calls
        optimizer.zero_grad();loss=partial_loss(x@coef.T+bias,target,w)+(coef.square().sum()/(2*strength*w.sum()))
        if not torch.isfinite(loss):raise ValueError('训练损失无效')
        loss.backward();calls+=1;return loss
    optimizer.step(closure)
    loss=closure();gradient=max(float(v.grad.detach().abs().max()) for v in (coef,bias))
    return {'coef':coef.detach().numpy().copy(),'intercept':bias.detach().numpy().copy(),
            'optimization':{'objective':float(loss.detach()),'maximum_gradient':gradient,'closure_calls':calls,'iterations':optimizer.state[coef]['n_iter'],'max_iter':max_iter,
                'stationarity_tolerance':1e-4,'stationarity_passed':gradient<=1e-4,'global_optimum_claimed':False}}


def probabilities(matrix,model):
    logits=np.asarray(matrix,np.float64)@model['coef'].T+model['intercept'];logits-=logits.max(axis=1,keepdims=True)
    values=np.exp(logits);return values/values.sum(axis=1,keepdims=True)


def standardize_frozen(matrix,mean,scale):
    """与当前StandardScaler相同：先把均值/尺度转为输入浮点类型，再原位计算。"""
    matrix=np.asarray(matrix);mean=np.asarray(mean);scale=np.asarray(scale)
    if matrix.ndim!=2 or matrix.dtype not in (np.dtype('float32'),np.dtype('float64')) or mean.shape!=(matrix.shape[1],) or scale.shape!=mean.shape:
        raise ValueError('标准化维度或输入类型无效')
    if not all(np.isfinite(v).all() for v in (matrix,mean,scale)) or np.any(scale<=0):raise ValueError('标准化参数无效')
    result=matrix.copy();result-=mean.astype(result.dtype);result/=scale.astype(result.dtype)
    return result


def metrics(rows,probability,classes,minimum=.35,margin=.05):
    """原标签、动作族相容与已确认叶类分别计分；拒判始终计入分母。"""
    probability=np.asarray(probability,np.float64)
    if len(classes)<2 or len(set(classes))!=len(classes) or probability.shape!=(len(rows),len(classes)):raise ValueError('评估输入/预测数目或类别维度不同')
    if not np.isfinite(probability).all() or np.any(probability<0) or np.any(probability>1) or not np.allclose(probability.sum(1),1.,atol=1e-6,rtol=0):raise ValueError('评估概率无效')
    results=[]
    for row,values in zip(rows,probability):
        order=np.argsort(values)[::-1];top=classes[order[0]];score=float(values[order[0]]);gap=score-float(values[order[1]])
        accepted=top if top!='unknown' and score>=minimum and gap>=margin else None
        results.append({k:row[k] for k in ('id','action_id','source_sha256','group','partition')}|{'top':top,'accepted':accepted,'score':score,'margin':gap,
            'label_scope':row['label_scope'],'legacy_correct':accepted==row['action_id'],'typed_compatible':accepted in row['label_scope']['allowed_actions']})
    return summarize_predictions(results,classes)


def summarize_predictions(results,classes):
    """允许在权重及预测不变时补充标签证据，不重复拟合或推理。"""
    positives=[r for r in results if r['action_id']!='unknown'];negatives=[r for r in results if r['action_id']=='unknown']
    per_class={}
    for action in classes:
        if action=='unknown':continue
        raw=[r for r in positives if r['action_id']==action]
        exact=[r for r in positives if r['label_scope']['leaf_label']==action]
        per_class[action]={'legacy_clips':len(raw),'legacy_correct':sum(r['legacy_correct'] for r in raw),'legacy_recall':sum(r['legacy_correct'] for r in raw)/len(raw) if raw else None,
            'verified_leaf_clips':len(exact),'verified_leaf_groups':len({r['group'] for r in exact}),
            'verified_leaf_correct':sum(r['accepted']==action for r in exact),'verified_leaf_recall':sum(r['accepted']==action for r in exact)/len(exact) if exact else None}
    accepted=[r for r in positives if r['accepted'] is not None]
    return {'scope':'cached fusion head only; no visual-family review or motion guard applied','positive_clips':len(positives),'negative_clips':len(negatives),
        'legacy_correct':sum(r['legacy_correct'] for r in positives),'legacy_accuracy_including_rejections':sum(r['legacy_correct'] for r in positives)/len(positives) if positives else None,
        'typed_compatible_correct':sum(r['typed_compatible'] for r in positives),'typed_compatible_accuracy':sum(r['typed_compatible'] for r in positives)/len(positives) if positives else None,
        'family_label_clips':sum(r['label_scope']['specificity']=='family' for r in positives),'accepted_positive_clips':len(accepted),
        'negative_false_accepts':sum(r['accepted'] is not None for r in negatives),'per_class':per_class,'predictions':results}
