"""只读既有真实语义/姿态缓存评估运动保护；不重跑GPU，不改发布头或数据标签。"""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment,project_path
configure_environment()
import argparse,collections,hashlib,json
import numpy as np
from fitness_app.motion_evidence import evaluate,DEFAULT,policy_signature

BASE=ROOT/'data/verification/step-22';OUT=ROOT/'data/verification/round3/motion-evidence-evaluation.json'


def recheck(evidence,config):
    if evidence['state'] in {'insufficient','static_exempt','hold_supported'}:return False
    signals=evidence['signals']
    if any(v['range']>=config['angle_degrees' if v['unit']=='image_degrees' else 'displacement_torso'] for v in signals.values()):return False
    families={name.split('_')[0] if name.startswith(('wrist_','ankle_')) else name for name in signals}
    return len(families)>=config['minimum_signal_families']


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--review-only',action='store_true');parser.add_argument('--refresh-published',action='store_true');args=parser.parse_args()
    if args.review_only:review();return
    data=json.loads((BASE/'semantic-video-features.json').read_text('utf8'));cal=json.loads((BASE/'video-release-calibration.json').read_text('utf8'))
    model=json.loads((ROOT/'data/models/semantic-action-classifier.json').read_text('utf8'))
    if model.get('motion_evidence') and not args.refresh_published:
        result=json.loads(OUT.read_text('utf8'))
        print('Preserved published evaluation snapshot; no recomputation/overwrite. Explicit --refresh-published requires republishing metadata afterwards.',flush=True)
        print(result['phase_reviewed_validation'],flush=True);return
    if model['sha256']!=cal['head_sha256']:raise ValueError('基线预测与当前发布头不一致')
    groups={part:{r['group'] for r in data['entries'] if r['partition']==part} for part in ('train','validation')}
    if groups['train']&groups['validation']:raise ValueError('训练和验证来源组交叉')
    predicted={r['id']:r for r in cal['validation']['predictions']};entries=[]
    review_path=ROOT/'data/datasets/development-movements/phase-reviews.json'
    phase_reviews=json.loads(review_path.read_text('utf8')) if review_path.exists() else {'reviews':[]}
    reviewed={r['id']:r for r in phase_reviews['reviews']}
    for row in data['entries']:
        semantic=project_path(row['cache']);pose=BASE/(row['id']+'-full-world-features.npz')
        with np.load(semantic,allow_pickle=False) as cache:
            if str(cache['signature'])!=row['feature_signature']:raise ValueError('原语义缓存签名改变')
            times=cache['timestamps'];start,end=int(times[0]),int(times[-1])
        phase=reviewed.get(row['id'])
        if phase and (any(phase[k]!=row[k] for k in ('source_sha256','group','partition','feature_signature')) or phase['sampled_source_timestamps_ms']!=times.tolist()):
            raise ValueError('阶段复核与实际采样身份不一致，不把旧复核套到新输入')
        with np.load(pose,allow_pickle=False) as cache:
            identity=json.loads(str(cache['identity']))
            if any(identity[k]!=row[k] for k in ('source_sha256','start_ms','end_ms','crop')):raise ValueError('姿态缓存来源改变')
            arrays={k:cache[k] for k in ('timestamps','points','sizes','people')}
            action=row['action_id'] if row['partition']=='train' else predicted[row['id']]['predicted']
            action=None if action=='unknown' else action
            evidence=evaluate(arrays,start,end,action)
        entries.append({'id':row['id'],'partition':row['partition'],'group':row['group'],'source_sha256':row['source_sha256'],
            'expected':row['action_id'],'reviewed_expected':phase['reviewed_action_id'] if phase else row['action_id'],
            'phase_review_applied':bool(phase),'action_before':action,'source_start_ms':start,'source_end_ms':end,'evidence':evidence})
    negative=[]
    path=ROOT/'data/verification/round3/upload-real-cache.npz'
    with np.load(path,allow_pickle=False) as cache:
        arrays={k:cache[k] for k in ('timestamps','points','sizes','people')}
        for decision in json.loads(str(cache['decisions_json'])):
            if 6000<=decision['start_ms'] and decision['end_ms']<=12000:
                raw_action=decision.get('raw_action_id') or decision['action_id']
                evidence=evaluate(arrays,decision['start_ms'],decision['end_ms'],raw_action)
                negative.append({'start_ms':decision['start_ms'],'end_ms':decision['end_ms'],'predicted':raw_action,'expected':None,'evidence':evidence})
    if not negative:raise ValueError('没有找到已复核的准备段，不用空集证明保护通过')
    candidates=[]
    # 阈值选择只使用训练正例与已观测的准备负例；验证标签不参与选择。
    training=[r for r in entries if r['partition']=='train' and r['reviewed_expected'] not in ('unknown',None)]
    training_negatives=negative+[r for r in entries if r['partition']=='train' and r['phase_review_applied'] and r['reviewed_expected'] is None]
    for angle in (5.,6.,8.,10.):
        for displacement in (.06,.08,.10,.12):
            config={**DEFAULT,'angle_degrees':angle,'displacement_torso':displacement}
            rejected=[r['id'] for r in training if recheck(r['evidence'],config)]
            correct_negatives=sum(recheck(r['evidence'],config) for r in training_negatives)
            candidates.append({'config':config,'train_veto_count':len(rejected),'train_veto_ids':rejected,'negative_veto_count':correct_negatives})
    selected=min(candidates,key=lambda r:(r['negative_veto_count']!=len(training_negatives),r['train_veto_count'],r['config']['angle_degrees'],r['config']['displacement_torso']))
    cfg=selected['config'];validation=[r for r in entries if r['partition']=='validation'];new_rejects=[];per_class={}
    for row in validation:
        veto=recheck(row['evidence'],cfg);after=None if veto else row['action_before'];row['veto']=veto;row['action_after']=after
        if row['expected']=='unknown':continue
        item=per_class.setdefault(row['expected'],{'clips':0,'correct_before':0,'correct_after':0,'newly_vetoed_correct':0})
        item['clips']+=1;item['correct_before']+=row['action_before']==row['expected'];item['correct_after']+=after==row['expected']
        item['newly_vetoed_correct']+=veto and row['action_before']==row['expected']
        if veto:new_rejects.append({k:row[k] for k in ('id','expected','action_before','action_after','evidence')})
    positives=sum(r['clips'] for r in per_class.values());before=sum(r['correct_before'] for r in per_class.values());after=sum(r['correct_after'] for r in per_class.values())
    for row in per_class.values():row['recall_before']=row['correct_before']/row['clips'];row['recall_after']=row['correct_after']/row['clips']
    actual_positive=[r for r in validation if r['reviewed_expected'] not in ('unknown',None)]
    actual_negative=[r for r in validation if r['reviewed_expected'] in ('unknown',None)]
    audited={'positive_clips':len(actual_positive),'negative_clips':len(actual_negative),
        'positive_correct_before':sum(r['action_before']==r['reviewed_expected'] for r in actual_positive),
        'positive_correct_after':sum(r['action_after']==r['reviewed_expected'] for r in actual_positive),
        'negative_false_accepts_before':sum(r['action_before'] is not None for r in actual_negative),
        'negative_false_accepts_after':sum(r['action_after'] is not None for r in actual_negative),
        'original_protocol_retained':True,'independent_validation':False}
    unreviewed_harm=[r['id'] for r in validation if r['veto'] and r['action_before']==r['expected'] and not (r['phase_review_applied'] and r['reviewed_expected'] is None)]
    report={'scope':'development_cache_motion_guard_not_independent_accuracy','base_model_version':model.get('base_version',model['version']),
        'feature_manifest_sha256':hashlib.sha256((BASE/'semantic-video-features.json').read_bytes()).hexdigest(),
        'selection':'training positives and already observed preparation negatives only; validation not used to select thresholds',
        'selected':selected,'candidate_count':len(candidates),'candidates':candidates,'policy_signature':policy_signature(cfg),
        'train_motion_states':dict(collections.Counter(r['evidence']['state'] for r in training)),
        'validation_motion_states':dict(collections.Counter(r['evidence']['state'] for r in validation)),
        'validation_positive_clips':positives,'correct_before':before,'correct_after':after,'accuracy_before':before/positives,'accuracy_after':after/positives,
        'per_class':per_class,'new_rejections':new_rejects,'known_preparation_negatives':negative,
        'training_vetoed_entries':[r for r in training if r['id'] in selected['train_veto_ids']],
        'phase_reviews':phase_reviews,'phase_reviewed_validation':audited,'unreviewed_correct_vetoes':unreviewed_harm,
        'training_negative_cases':len(training_negatives),
        'new_gpu_inferences':0,'published_weights_changed':False,'runtime_enabled':False,
        'safe_nonregression_candidate':not unreviewed_harm and audited['positive_correct_after']==audited['positive_correct_before'] and
            audited['negative_false_accepts_after']<=audited['negative_false_accepts_before'] and selected['negative_veto_count']==len(training_negatives)}
    OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps({k:report[k] for k in ('base_model_version','correct_before','correct_after','train_motion_states','validation_motion_states','safe_nonregression_candidate')},ensure_ascii=False),flush=True)
    print('Selected',cfg,'train veto',selected['train_veto_count'],'negative veto',selected['negative_veto_count'],'validation veto ids',[r['id'] for r in new_rejects],flush=True)
    print('Phase-audited validation',audited,'unreviewed harm',unreviewed_harm,flush=True)


def review():
    from PIL import Image,ImageDraw,ImageOps
    from review_round3_failures import images
    report=json.loads(OUT.read_text('utf8'));features=json.loads((BASE/'semantic-video-features.json').read_text('utf8'))
    entries={r['id']:r for r in features['entries']};rows=report['training_vetoed_entries']+report['new_rejections']
    for phase in report.get('phase_reviews',{}).get('reviews',[]):
        if not any(r['id']==phase['id'] for r in rows):rows.append({'id':phase['id'],'expected':phase['publisher_action_id']})
    sheet=Image.new('RGB',(1280,220*len(rows)),'white');draw=ImageDraw.Draw(sheet)
    for index,item in enumerate(rows):
        entry=entries[item['id']]
        with np.load(project_path(entry['cache']),allow_pickle=False) as cache:times=cache['timestamps'][[0,2,5,7]].tolist()
        pictures,actual=images(entry,times)
        draw.text((8,index*220+5),item['id']+' | '+item['expected']+' | '+entry['partition'],fill='black')
        for column,picture in enumerate(pictures):
            picture=ImageOps.contain(picture,(310,178));sheet.paste(picture,(column*320+(320-picture.width)//2,index*220+25))
            draw.text((column*320+8,index*220+205),'source '+str(actual[column])+' ms',fill='black')
    target=OUT.with_name('motion-evidence-review.jpg');sheet.save(target,quality=90)
    print('Saved',target.relative_to(ROOT))


if __name__=='__main__':main()
