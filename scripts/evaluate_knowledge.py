"""固定90题开发/留出分组评测；仅检索，无生成API。"""
import json,sys,time,argparse
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment
configure_environment()
from fitness_app.knowledge_store import KnowledgeStore,VERSION

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output');parser.add_argument('--compare-keyword',action='store_true');args=parser.parse_args()
    rows=json.loads((ROOT/'tests/knowledge_queries.json').read_text(encoding='utf-8'));store=KnowledgeStore();results=[]
    if args.compare_keyword and store.vectors is not None:store.encode(['检索回归准备'])
    keyword=KnowledgeStore(index_dir='第五轮优化/.verification/no-index') if args.compare_keyword else None
    for row in rows:
        started=time.monotonic();value=store.search(row['query'],row['domain'],row['action_id']);titles=[x['title'] for x in value['matches']]
        baseline=keyword.search(row['query'],row['domain'],row['action_id']) if keyword else None
        results.append({**({'keyword_matches':[x['title'] for x in baseline['matches']],'keyword_hit':bool(set(x['title'] for x in baseline['matches'])&set(row['expected_titles'])),'keyword_abstained':not baseline['matches']} if baseline else {}),**row,'matches':titles,'hit':bool(set(titles)&set(row['expected_titles'])),'abstained':not titles,'mode':value['retrieval_mode'],'ms':round((time.monotonic()-started)*1000,2)})
    stats={}
    for group in ['movement','nutrition']:
        for split in ['dev','holdout']:
            selected=[x for x in results if ('movement' if x['domain']=='movement' else 'nutrition')==group and x['split']==split]
            answerable=[x for x in selected if x['answerable']];empty=[x for x in selected if not x['answerable']]
            stats[group+'_'+split]={'recall_hits':sum(x['hit'] for x in answerable),'recall_total':len(answerable),'no_answer_correct':sum(x['abstained'] for x in empty),'no_answer_total':len(empty)}
    previous=ROOT/'data/knowledge/evaluation.json'
    history=json.loads(previous.read_text(encoding='utf-8')).get('history',[]) if previous.exists() else []
    output={'history':history,'evaluation_note':'同一固定题重复检查，非新增独立留出','version':VERSION,'questions':len(rows),'stats':stats,'results':results,'not_generation_or_visual_accuracy':True}
    if keyword:
        for name in list(stats):
            group,split=name.rsplit('_',1);selected=[x for x in results if ('movement' if x['domain']=='movement' else 'nutrition')==group and x['split']==split];answerable=[x for x in selected if x['answerable']];empty=[x for x in selected if not x['answerable']]
            stats[name]['keyword']={'recall_hits':sum(x['keyword_hit'] for x in answerable),'recall_total':len(answerable),'no_answer_correct':sum(x['keyword_abstained'] for x in empty),'no_answer_total':len(empty)}
    from fitness_app.paths import project_path
    target=project_path(args.output) if args.output else ROOT/'data/knowledge/evaluation.json'
    if not target.parent.exists():raise RuntimeError('评测输出父目录不存在，未创建近似路径')
    output.update(actual_modes=sorted({x['mode'] for x in results}),paid_api_calls=0,new_independent_questions=False)
    target.write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(stats,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
