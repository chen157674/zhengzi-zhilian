"""片段来源标签指标：拒判留在主分母，缺真值不返回假零分。"""
from collections import Counter,defaultdict


def _ratio(numerator,denominator):return numerator/denominator if denominator else None


def _summary(rows):
    counts=Counter((row['truth'],row['prediction']) for row in rows)
    by_class=[]
    for label in (0,1):
        tp=counts[label,label];fp=counts[1-label,label];support=sum(counts[label,prediction] for prediction in (0,1,None))
        fn=support-tp;rejected=counts[label,None]
        by_class.append({'class':label,'support':support,'predicted_positive':tp+fp,'TP':tp,'FP':fp,'FN_including_rejections':fn,
            'rejected':rejected,'precision':_ratio(tp,tp+fp),'recall':_ratio(tp,support),
            'f1':_ratio(2*tp,2*tp+fp+fn) if support else None,'coverage':_ratio(support-rejected,support)})
    n=len(rows);covered=sum(row['prediction'] is not None for row in rows)
    macro=sum(item['f1'] for item in by_class)/2 if all(item['f1'] is not None for item in by_class) else None
    return {'samples':n,'covered':covered,'rejected':n-covered,'coverage':_ratio(covered,n),'accuracy':_ratio(counts[0,0]+counts[1,1],n),
        'macro_f1':macro,'positive_precision':by_class[1]['precision'],'positive_recall':by_class[1]['recall'],'classes':by_class,
        'confusion':{'truth_rows':[0,1],'prediction_columns':[0,1,'rejected'],'counts':[[counts[truth,pred] for pred in (0,1,None)] for truth in (0,1)]},
        'rejection_reasons':dict(Counter(row.get('reason') or 'UNSPECIFIED' for row in rows if row['prediction'] is None))}


def evaluate(rows):
    if not rows:return {'status':'evidence_insufficient','reason':'没有可评价样本','main':None,'conditional':None}
    if any(type(row.get('truth')) is not int or row['truth'] not in (0,1) for row in rows):
        return {'status':'evidence_insufficient','reason':'缺少对应任务的独立来源标签','main':None,'conditional':None}
    if any(row.get('prediction') is not None and (type(row['prediction']) is not int or row['prediction'] not in (0,1)) for row in rows):raise ValueError('预测须为 0、1 或明确拒判 None')
    keys=[row['sample_key'] for row in rows]
    if len(keys)!=len(set(keys)):raise ValueError('评价样本重复，分母不合法')
    main=_summary(rows);conditional_rows=[row for row in rows if row['prediction'] is not None]
    grouped={}
    for field in ('subject_group','view'):
        groups=defaultdict(list)
        for row in rows:groups[row.get(field,'unknown')].append(row)
        grouped[field]={name:_summary(items) for name,items in sorted(groups.items())}
    return {'status':'computed','main':main,'conditional':_summary(conditional_rows) if conditional_rows else None,'groups':grouped,
        'acceptance_passed':bool(main['positive_precision'] is not None and main['positive_precision']>=.85 and main['positive_recall']>=.8
            and main['macro_f1'] is not None and main['macro_f1']>=.8 and main['coverage']>=.8
            and all(item['coverage'] is not None and item['coverage']>=.7 for item in main['classes']))}
