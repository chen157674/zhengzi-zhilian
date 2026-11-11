"""人工整理的有来源知识；本地BM25/向量融合，不包含个人记录。"""
import hashlib,json,math,re,threading,secrets,time,copy
from collections import Counter
from pathlib import Path
from .paths import ROOT,project_path

VERSION='fitness-knowledge-20261030-1'
MODEL_REVISION='7999e1d3359715c523056ef9478215996d62a620'
DOMAINS={'movement','nutrition','hypertrophy'}
MODEL_RUNTIME_FILES=frozenset({'config.json','model.safetensors','special_tokens_map.json','tokenizer.json','tokenizer_config.json','vocab.txt'})

def verify_encoder_files(path,hashes):
    if not isinstance(hashes,dict) or not MODEL_RUNTIME_FILES<=set(hashes):raise ValueError('检索模型身份清单缺少推理文件')
    for name in MODEL_RUNTIME_FILES:
        candidate=project_path(path/name)
        if candidate.parent!=path:raise ValueError('检索模型文件路径不符')
        with candidate.open('rb') as stream:actual=hashlib.file_digest(stream,'sha256').hexdigest()
        if actual!=hashes[name]:raise ValueError('检索模型文件身份不符')

SOURCES={
 'S06':{'title':'ACE Seated Chest Press','url':'https://www.acefitness.org/resources/everyone/exercise-library/188/seated-chest-press/','reuse':'原创事实摘要与链接；不分发原页或素材'},
 'S07':{'title':'ACE Seated Biceps Curl','url':'https://www.acefitness.org/resources/everyone/exercise-library/44/seated-biceps-curl/','reuse':'仅哑铃共同原则；坐姿要求不转成站姿要求'},
 'S08':{'title':'ACE Bodyweight Squat','url':'https://www.acefitness.org/resources/everyone/exercise-library/135/bodyweight-squat/','reuse':'原创事实摘要与链接'},
 'S09':{'title':'ISSN protein and exercise (2017)','url':'https://pmc.ncbi.nlm.nih.gov/articles/PMC5477153/','reuse':'CC BY 4.0，中文整理；Jäger等，DOI 10.1186/s12970-017-0177-8','license_url':'https://creativecommons.org/licenses/by/4.0/'},
 'S10':{'title':'FAO/WHO/UNU Human energy requirements (2004)','url':'https://www.fao.org/4/y5686e/y5686e07.htm','reuse':'公式事实与原创摘要；表5.2/5.3、5.3节'},
 'S11':{'title':'Mifflin等静息能量方程 (1990)','url':'https://pubmed.ncbi.nlm.nih.gov/2305711/','reuse':'仅摘要事实与书目信息；不分发全文'},
 'S12':{'title':'WHO Healthy diet (2026-01-26)','url':'https://www.who.int/news-room/fact-sheets/detail/healthy-diet','reuse':'原创事实摘要与链接'},
 'S14':{'title':'Iraki等非赛季自然健美营养综述 (2019)','url':'https://pmc.ncbi.nlm.nih.gov/articles/PMC6680710/','reuse':'CC BY 4.0，中文整理；DOI 10.3390/sports7070154','license_url':'https://creativecommons.org/licenses/by/4.0/'},
 'P01':{'title':'本项目观察与复测规则','url':None,'reuse':'project_rule；工程条件，不冒充外部阈值'},
}
# 每条为人工核对来源后的原创摘要，source定位随条目保存。无站姿原文时仅复用共同原则。
SEEDS=[
 ('movement','machine_chest_press','座椅与把手高度','推胸起始时调整座椅，使把手位于胸部附近；具体设置需匹配器械。','S06','Step 1'),
 ('movement','machine_chest_press','脚部支撑','推胸准备时双脚稳放地面，建立可控制的支撑。','S06','Step 1'),
 ('movement','machine_chest_press','背部支撑','常规有靠背坐姿推胸保持合适支撑；离靠背或单侧专项变式须另行识别。','S06','Steps 1–4'),
 ('movement','machine_chest_press','闭合握持原则','推胸知识建议完整握住把手；知道握法原则不能证明当前画面拇指绕握或手掌接触正确。','S06','Step 1'),
 ('movement','machine_chest_press','推胸手腕方向','常规推胸保持手腕与前臂较自然对齐，避免明显折腕；可见投影不等于握力。','S06','Steps 1–2'),
 ('movement','machine_chest_press','推胸回程','推胸返回阶段缓慢控制把手，不用快速回弹替代主动控制。','S06','Downward phase'),
 ('movement','machine_chest_press','双侧与单侧变式','推胸可以存在单侧变式，不能把单侧或交替练习直接按双侧不同步判断。','S06','Variation'),
 ('movement','machine_chest_press','推胸观察机位','按当前规则适用机位观察侧倾或双臂关系，器械遮挡时允许未知，不强行给出正常结论。','P01','posture_rules / movement_guidance'),
 ('movement','machine_chest_press','推胸单项复测','从本人真实问题选一项调整，在相同动作、侧别、规则和可比机位下复测。','P01','retests policy'),
 ('movement','machine_chest_press','握持知识与检测','闭合握持和手腕方向是不同概念，现有身体关键点不能证明手掌握稳或拇指绕握。','P01','capability boundary'),
 ('movement','standing_dumbbell_curl','弯举握持共同原则','普通哑铃弯举的旋后握法与锤式中立握法属于不同变式，不混为同一动作条件。','S07','Step 1 / Variation'),
 ('movement','standing_dumbbell_curl','弯举手腕','在个人舒适姿势内让手腕与前臂对齐，不为了哑铃紧贴身体而强行弯手腕。','S07','Final paragraph'),
 ('movement','standing_dumbbell_curl','弯举抬起控制','哑铃弯举抬起时缓慢屈肘，避免把一般控制原则误当瞬间角度阈值。','S07','Step 2'),
 ('movement','standing_dumbbell_curl','弯举下放控制','哑铃弯举下放时主动控制返回起点，而不是任由重量快速掉落。','S07','Step 3'),
 ('movement','standing_dumbbell_curl','弯举肘前移的变式','标准控制与特定允许肘前移的变式要分开，不能把任何肘前移都标错。','S07','Step 4'),
 ('movement','standing_dumbbell_curl','坐姿资料适用边界','坐姿哑铃弯举原文的背靠要求不适用于站姿；这里只复用握法、手腕和回程的共同原则。','S07','Starting Position'),
 ('movement','standing_dumbbell_curl','站姿躯干借力观察','当前弯举规则只对适用机位下明显躯干前后摆动作参考；主动借力专项变式不套用。','P01','curl.trunk_sway'),
 ('movement','standing_dumbbell_curl','上臂摆动观察','普通受控弯举可观察上臂明显摆动，提示尝试稳定上臂，不能据此判断肌肉受力或伤病。','P01','curl.upper_arm_swing'),
 ('movement','standing_dumbbell_curl','交替弯举同步边界','主动交替动作不能使用双侧同时动作的同步条件进行纠错。','P01','movement guidance policy'),
 ('movement','standing_dumbbell_curl','弯举复测条件','只比较同一弯举变式、侧别和可比机位的同项结果，未知时段不计正常。','P01','retests policy'),
 ('movement','bodyweight_squat','徒手深蹲起始','先建立平衡、可控制的站姿，再开始屈髋屈膝下蹲。','S08','Starting Position'),
 ('movement','bodyweight_squat','深蹲下行控制','下蹲阶段控制髋膝共同运动，不用突然下落替代可控制的动作。','S08','Downward phase'),
 ('movement','bodyweight_squat','深蹲上行控制','起身阶段保持可控制的整体动作，按当前能力完成，不强追统一幅度。','S08','Upward phase'),
 ('movement','bodyweight_squat','深蹲可控幅度','下蹲深度结合个人可控制、无不适的活动范围和训练变式，不以越深越好作为统一判断。','P01','squat.range'),
 ('movement','bodyweight_squat','浅蹲计划','计划明确采用浅蹲时，不能直接套完整深蹲幅度提示作错误结论。','P01','squat.range exception'),
 ('movement','bodyweight_squat','膝过脚尖边界','不能仅因膝盖超过脚尖就判定深蹲错误；须结合具体动作与实际可评估证据。','P01','capability boundary'),
 ('movement','bodyweight_squat','深蹲观察机位','当前幅度判项需要侧面有效观察；正面或遮挡不能凭知识补出幅度检测。','P01','squat.range views'),
 ('movement','bodyweight_squat','未检出不等于正确','深蹲没有问题提示，只表示可评估范围未观察到该项，不代表全部动作正确。','P01','retests policy'),
 ('movement','bodyweight_squat','深蹲比较单位','按周期和按时间的观察不能直接相减或生成统一提升百分比。','P01','retests evaluate'),
 ('movement','bodyweight_squat','深蹲复测覆盖','工程复测沿用至少3个完整有效周期或2000毫秒观察及80%覆盖；不是外部机构医学标准。','P01','retests MINIMUM_*'),
 ('nutrition',None,'均衡饮食','一般饮食强调多样、均衡、适量与营养充足；具体组合结合个人生活与需求。','S12','Key facts'),
 ('nutrition',None,'食物多样性','通过多样食物改善营养覆盖，而不是把单一种食物当作全部营养来源。','S12','Healthy diet principles'),
 ('nutrition',None,'蔬果与膳食纤维','均衡饮食重视蔬菜水果及含膳食纤维的食物，不能用单次训练记录推断实际摄入。','S12','Fruit and vegetables / fibre'),
 ('nutrition',None,'食物选择','可围绕蔬果、全谷物、豆类等多样食物选择组织一般饮食解释，不自动生成个人食谱。','S12','Overview'),
 ('nutrition',None,'不同阶段不同需求','饮食需求与年龄、生活阶段和活动有关，成人方法不自动适用于儿童或特殊阶段。','S12','Overview'),
 ('nutrition',None,'蛋白质与运动范围','多数健康规律运动者可参考每日1.4—2.0克每公斤体重；这是范围，不是每个人精确需求。','S09','Position statement'),
 ('nutrition',None,'蛋白质计算单位','采用体重公斤乘每天每公斤克数，结果是克/天，不能把斤直接当公斤。','P01','nutrition method units'),
 ('nutrition',None,'蛋白质并非实际摄入','计算出的蛋白质范围是参考，不证明用户今天已经吃了多少。','P01','nutrition semantics'),
 ('nutrition',None,'蛋白质特殊范围','减重竞技等特定情形的更高蛋白质范围不能默认应用于所有一般运动用户。','S09','Position statement'),
 ('nutrition',None,'蛋白质来源','运动蛋白质知识应结合整体饮食和食物选择，不把蛋白粉视为唯一来源。','S09','Protein sources'),
 ('nutrition',None,'基础能量与总能量','BMR是基础能量估计；乘整体活动PAL得到每日总能量参考，两个值不能用同一名称。','S10','5.2 / 5.3'),
 ('nutrition',None,'整体活动水平','PAL依据长期整体生活活动，包括工作、通勤和休闲，不是每周练几次的简单换算。','S10','5.3.1'),
 ('nutrition',None,'维持热量范围','基础能量乘对应活动区间给维持参考；活动区间不是个人95%置信区间。','S10','Table 5.3 / 5.3'),
 ('nutrition',None,'避免重复增加训练热量','若已用整体活动PAL估计总能量，不再把视频训练消耗重复加到同一结果。','P01','energy method policy'),
 ('nutrition',None,'静息能量公式区别','Mifflin方程预测静息能量REE，其原研究不是个人每日总摄入量公式。','S11','Abstract'),
 ('hypertrophy',None,'增肌与能量平衡','自然健美非赛季综述讨论正能量平衡与阻力训练的配合，不能据此保证个人增肌效果。','S14','Positive Energy Balance'),
 ('hypertrophy',None,'增肌盈余适用人群','综述10%—20%盈余主要针对初中级自然健美非赛季人群，不默认外推全部校园用户。','S14','Abstract / Energy'),
 ('hypertrophy',None,'进阶者保守调整','该综述建议训练水平更高的健美者更保守地增加能量与体重；不是统一加500千卡。','S14','Energy'),
 ('hypertrophy',None,'蛋白质范围来源区别','健美综述与一般运动共识的人群、范围不同，不应机械平均成新公式。','S14','Protein'),
 ('hypertrophy',None,'恢复与饮食配合','增肌解释要考虑训练背景、总饮食和恢复，单段姿态视频不能测出肌肉增长或个人营养需求。','P01','scope boundary'),
]

def entries():
    result=[]
    for i,(domain,action,title,text,source,locator) in enumerate(SEEDS,1):
        value={'id':f'k:kb:{i:03d}','domain':domain,'action_id':action,'title':title,'text':text,
               'source_id':source,'source':SOURCES[source],'locator':locator,'version':VERSION,
               'source_type':'project_rule' if source=='P01' else 'external_summary','reviewed_on':'2026-10-30',
               'status':'published','variant_scope':'shared_dumbbell_principles' if source=='S07' else None}
        value['sha256']=hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        result.append(value)
    return result

def terms(text):
    text=text.lower();chars=re.findall(r'[\u4e00-\u9fff]',text)
    return chars+[a+b for a,b in zip(chars,chars[1:])]+re.findall(r'[a-z0-9_.]+',text)

class KnowledgeStore:
    def __init__(self,index_dir=None):
        self.rows=entries();self.by_id={r['id']:r for r in self.rows};self.lock=threading.RLock();self.encoder_lock=threading.RLock()
        self.docs=[Counter(terms(r['title']+' '+r['text'])) for r in self.rows]
        self.avg=sum(map(lambda x:sum(x.values()),self.docs))/len(self.docs)
        self.df=Counter(term for doc in self.docs for term in doc)
        self.index_dir=project_path(index_dir or 'data/knowledge/index');self.encoder=None;self.vectors=None
        self.model_hashes=None;self.index_status='keyword_only';self.encoder_preparing=False;self.encoder_error=None;self.closed=False;self.prepare_thread=None;self._load_index()
    def _load_index(self):
        manifest=self.index_dir/'manifest.json'
        if not manifest.exists():return
        try:
            import numpy as np
            info=json.loads(manifest.read_text(encoding='utf-8'));path=project_path(self.index_dir/info['vectors_file'])
            if path.parent!=self.index_dir:raise ValueError('index path')
            expected=hashlib.sha256(json.dumps(self.rows,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
            if info['knowledge_sha256']!=expected or info['model_revision']!=MODEL_REVISION or hashlib.sha256(path.read_bytes()).hexdigest()!=info['vectors_sha256']:raise ValueError('index identity')
            vectors=np.load(path,allow_pickle=False)
            if vectors.shape!=(len(self.rows),512) or not np.isfinite(vectors).all() or not np.allclose(np.linalg.norm(vectors,axis=1),1,atol=1e-4):raise ValueError('vectors')
            self.vectors=vectors;self.model_hashes=info['model_files'];self.index_status='hybrid_ready'
        except (OSError,ValueError,KeyError):self.index_status='keyword_index_invalid'
    def encode(self,texts,query=False):
        with self.encoder_lock:
            if self.encoder is None:
                import torch
                from transformers import AutoTokenizer,AutoModel
                path=project_path('data/models/bge-small-zh-v1.5')
                if self.model_hashes:
                    verify_encoder_files(path,self.model_hashes)
                tokenizer=AutoTokenizer.from_pretrained(path,local_files_only=True,trust_remote_code=False)
                model=AutoModel.from_pretrained(path,local_files_only=True,trust_remote_code=False).eval().to('cpu')
                self.encoder=(tokenizer,model,torch)
            tokenizer,model,torch=self.encoder
            if query:texts=['为这个句子生成表示以用于检索相关文章：'+x for x in texts]
            batch=tokenizer(texts,padding=True,truncation=False,return_tensors='pt')
            if batch['input_ids'].shape[1]>512:raise ValueError('知识文本超过本地模型长度')
            with torch.inference_mode():vectors=torch.nn.functional.normalize(model(**batch).last_hidden_state[:,0],p=2,dim=1)
            return vectors.cpu().numpy()
    def prepare_encoder(self):
        # 首个用户查询只触发一次后台准备，本地关键词结果不等待大型库冷导入。
        if self.closed or self.encoder is not None or self.encoder_preparing or self.encoder_error:return
        self.encoder_preparing=True
        def prepare():
            try:self.encode(['检索准备'])
            except (OSError,ValueError,ImportError,RuntimeError) as error:self.encoder_error=type(error).__name__
            finally:self.encoder_preparing=False
        self.prepare_thread=threading.Thread(target=prepare,daemon=True,name='knowledge-encoder-prepare');self.prepare_thread.start()

    def search(self,query,domain,action_id=None,limit=5):
        if not isinstance(query,str) or not query.strip() or len(query)>1000 or not isinstance(domain,str) or domain not in DOMAINS or type(limit) is not int or not 1<=limit<=5:raise ValueError('知识查询参数无效')
        if action_id is not None and (not isinstance(action_id,str) or action_id not in {'machine_chest_press','standing_dumbbell_curl','bodyweight_squat'}):raise ValueError('动作不在知识范围')
        rejected=False
        # 产品范围与明确动作是硬条件，语义相似不能覆盖它们。
        if re.search(r'诊断|撕裂|损伤程度|脊柱局部曲度|个性化处方|营养处方|肾病|类固醇|握力数值|器械配重|哑铃多少公斤',query):rejected=True
        if domain=='movement' and action_id:
            aliases={'machine_chest_press':r'推胸','standing_dumbbell_curl':r'弯举|锤式','bodyweight_squat':r'深蹲|下蹲'}
            if any(k!=action_id and re.search(v,query) for k,v in aliases.items()):rejected=True
            if re.search(r'面拉|硬拉|划船|绳索|杠铃',query):rejected=True
        if rejected:return {'version':VERSION,'retrieval_mode':'scope_filter','matches':[],'status':'no_match','domain':domain,'action_id':action_id}
        query=query.replace('次数','周期').replace('秒数','时间')
        allowed=[i for i,r in enumerate(self.rows) if self.by_id.get(r['id'],{}).get('status')=='published' and r['domain']==domain and (domain!='movement' or action_id is None or r['action_id']==action_id)]
        words=Counter(terms(query));scores={}
        for i in allowed:
            doc=self.docs[i];length=sum(doc.values());score=0
            for word in words:
                tf=doc.get(word,0)
                if tf:score+=math.log(1+(len(self.rows)-self.df[word]+.5)/(self.df[word]+.5))*tf*2.2/(tf+1.2*(.25+.75*length/self.avg))
            if score>0 and any(len(w)>1 and w in doc for w in words):scores[i]=score
        keyword=sorted(scores,key=lambda i:(-scores[i],i))[:20];ranks={i:1/(60+j+1) for j,i in enumerate(keyword)}
        mode='keyword'
        if self.vectors is not None and allowed and self.encoder is None:
            self.prepare_encoder();mode='keyword_fallback' if self.encoder_error else 'keyword_warming_up'
        elif self.vectors is not None and allowed:
            try:
                import numpy as np
                similarity=self.vectors@self.encode([query],True)[0]
                # 相似度不是置信度；无字面支持且相关性不足时不强塞条目。
                semantic=sorted([i for i in allowed if similarity[i]>=.55],key=lambda i:(-float(similarity[i]),i))[:20]
                for j,i in enumerate(semantic):ranks[i]=ranks.get(i,0)+1/(60+j+1)
                mode='hybrid'
            except (OSError,ValueError,ImportError,RuntimeError):mode='keyword_fallback'
        chosen=sorted(ranks,key=lambda i:(-ranks[i],i))[:limit]
        return {'version':VERSION,'retrieval_mode':mode,'matches':[self.public_row(self.by_id[self.rows[i]['id']]) for i in chosen],
                'status':'matched' if chosen else 'no_match','domain':domain,'action_id':action_id}

    def public_row(self,item):
        row=copy.deepcopy(item)
        row.update(source_status=row.get('status','unavailable'),published_version=row['version'],
                   support_scope={'domain':row['domain'],'action_id':row.get('action_id'),'variant':row.get('variant_scope')})
        return row

    def published(self,chunk_id,version=None):
        with self.lock:
            row=self.by_id.get(chunk_id)
            if row is None:return None
            if version is not None and version!=row['version']:return {'id':chunk_id,'title':row['title'],'status':'version_unavailable','version':version}
            if row.get('status')!='published':return {'id':chunk_id,'title':row['title'],'status':'withdrawn','version':row['version']}
            return self.public_row(row)

    def catalog(self):
        from .catalogue import definitions
        with self.lock:
            rows=[self.public_row(r) for r in self.by_id.values() if r.get('status')=='published']
        labels={'movement':'动作知识','nutrition':'饮食知识','hypertrophy':'增肌与恢复'}
        return {'version':VERSION,'index_status':self.index_status,
                'domains':[{'id':key,'label':labels[key],'topics':list(dict.fromkeys(r['title'] for r in rows if r['domain']==key)),
                            'available_count':sum(r['domain']==key for r in rows)} for key in sorted(DOMAINS)],
                'actions':[{'action_id':a['action_id'],'name':a['name'],'equipment':a.get('equipment',''),'aliases':a.get('aliases',[]),'knowledge_count':sum(r['action_id']==a['action_id'] for r in rows)} for a in definitions()],
                'example_queries':[{'query':r['title'],'domain':r['domain'],'action_id':r['action_id']} for r in rows[::10]]}

    def retrieval(self,query,domain,action_id=None,limit=5):
        with self.lock:
            result=self.search(query,domain,action_id,limit)
        return {**result,'retrieval_id':secrets.token_urlsafe(18),'expires_at_ms':int(time.time()*1000)+1800000}

    def stop(self):
        self.closed=True
        if self.prepare_thread and self.prepare_thread is not threading.current_thread():self.prepare_thread.join(2)
