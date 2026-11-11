"""复用本机MIT CLIP生成动作文本向量；此过程不是监督训练。"""
import hashlib,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import configure_environment
configure_environment()
from fitness_app.gym_recognition import MODEL,MANIFEST,PROMPTS,CONFIG,signature,resources
from fitness_app.visual_recognition import DIRECTORY,normalize


def run():
    existing,_=resources()
    if existing:print(json.dumps({'reused':True,'version':existing['version']}));return
    import numpy as np
    import onnxruntime as ort
    from tokenizers import Tokenizer
    backbone=json.loads((DIRECTORY/'manifest.json').read_text(encoding='utf-8'))
    for name in ('tokenizer.json','onnx/text_model_quantized.onnx'):
        if hashlib.sha256((DIRECTORY/name).read_bytes()).hexdigest()!=backbone['files'][name]['sha256']:raise ValueError('模型校验失败')
    tokenizer=Tokenizer.from_file(str(DIRECTORY/'tokenizer.json'));tokenizer.enable_truncation(max_length=77)
    tokenizer.enable_padding(length=77,pad_id=tokenizer.token_to_id('<|endoftext|>'),pad_token='<|endoftext|>')
    templates=CONFIG['prompt_templates'];texts=[t.format(p) for p in PROMPTS.values() for t in templates]
    ids=np.asarray([item.ids for item in tokenizer.encode_batch(texts)],dtype=np.int64)
    options=ort.SessionOptions();options.intra_op_num_threads=2;options.inter_op_num_threads=1
    text=ort.InferenceSession(str(DIRECTORY/'onnx/text_model_quantized.onnx'),options,providers=['CPUExecutionProvider'])
    features=normalize(normalize(text.run(['text_embeds'],{'input_ids':ids})[0]).reshape(len(PROMPTS),len(templates),-1).mean(axis=1))
    np.savez_compressed(MODEL,features=normalize(features),labels=np.asarray(list(PROMPTS)))
    info={'schema_version':1,'signature':signature(),'version':'gym-zero-shot-1-'+signature()[:12],
          'sha256':hashlib.sha256(MODEL.read_bytes()).hexdigest(),'backbone_revision':backbone['revision'],
          'classification_method':'zero_shot','supervised_training':False,'independent_validation_passed':False,
          'license':'MIT; existing OpenAI CLIP / Xenova conversion','config':CONFIG,'prompts':PROMPTS}
    MANIFEST.write_text(json.dumps(info,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'classes':len(PROMPTS),'supervised_training':False,'bytes':MODEL.stat().st_size}))


if __name__=='__main__':run()
