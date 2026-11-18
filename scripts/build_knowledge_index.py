"""仅构建已复核公共知识的本地索引，固定版本并验证模型文件。"""
import hashlib,json,os,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment,project_path
configure_environment()
from fitness_app.knowledge_store import KnowledgeStore,MODEL_REVISION,VERSION,MODEL_RUNTIME_FILES
import numpy as np

def main():
    store=KnowledgeStore();folder=project_path('data/knowledge/index');folder.mkdir(parents=True,exist_ok=True)
    rows=store.rows;vectors=store.encode([r['title']+'。'+r.get('question','')+'。'+r['text'] for r in rows])
    if vectors.shape!=(len(rows),512) or not np.isfinite(vectors).all():raise ValueError('编码结果无效')
    model=project_path('data/models/bge-small-zh-v1.5')
    info={'version':VERSION,'model_revision':MODEL_REVISION,'model_files':{name:hashlib.sha256((model/name).read_bytes()).hexdigest() for name in sorted(MODEL_RUNTIME_FILES)},
          'knowledge_sha256':hashlib.sha256(json.dumps(rows,ensure_ascii=False,sort_keys=True).encode()).hexdigest(),
          'count':len(rows),'dimensions':512,'license':'MIT','retrieval':'BM25+L2-CLS+RRF60'}
    pending=folder/'vectors.pending'
    try:
        with pending.open('wb') as out:np.save(out,vectors,allow_pickle=False)
        info['vectors_sha256']=hashlib.sha256(pending.read_bytes()).hexdigest()
        info['vectors_file']='vectors-'+info['vectors_sha256'][:16]+'.npy'
        pending.replace(folder/info['vectors_file'])
        text=folder/'manifest.pending';text.write_text(json.dumps(info,ensure_ascii=False,indent=2),encoding='utf-8');text.replace(folder/'manifest.json')
    finally:
        if pending.exists():pending.unlink()
    print(json.dumps({'entries':len(rows),'dimensions':512,'model_revision':MODEL_REVISION,'index':str(folder)},ensure_ascii=False))
if __name__=='__main__':main()
