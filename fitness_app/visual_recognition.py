"""本地CLIP视觉开发候选；不由生产预览加载，不代表姿态纠错真值。"""
import hashlib
import json
import numpy as np
from PIL import Image
from .paths import ROOT,project_path
from .content_bounds import detect_content_bounds

DIRECTORY=ROOT/'data/models/clip-vit-base-patch32'
PROMPTS={
    'bodyweight_squat':'a person doing a bodyweight squat',
    'standard_push_up':'a person doing a push-up with hands on the floor',
    'reverse_lunge':'a person stepping backwards into a reverse lunge',
    'bodyweight_glute_bridge':'a person lying on their back doing a glute bridge with bent knees',
    'bent_knee_sit_up':'a person doing a sit-up with bent knees and lifting their torso off the floor',
    'forearm_plank':'a person holding a plank on their forearms and toes',
    'jumping_jack':'a person doing jumping jacks with arms and legs spread apart',
    'high_knees':'a person running in place with high knees',
    'standing_dumbbell_curl':'a person standing and doing a dumbbell biceps curl',
    'barbell_overhead_press':'a person standing and pressing a barbell overhead',
    'barbell_bent_over_row':'a person bent over and rowing a barbell towards their abdomen',
    'conventional_deadlift':'a person doing a conventional deadlift with a barbell from the floor',
    'unknown_standing':'a person standing still and resting between exercises',
    'unknown_walking':'a person walking through a room',
    'unknown_talking':'people talking and explaining an exercise without exercising',
    'unknown_sitting':'a person sitting still and resting',
    'unknown_empty':'an empty exercise room without people',
}
TEMPLATES=('A photo of {}.','A video frame of {}.','An exercise demonstration of {}.')
CONFIG={'schema':'clip-visual-reference-1','short_edge':224,'minimum_cosine':.20,'minimum_margin':.02,
        'remove_encoding_borders':True,'templates':TEMPLATES,'prompts':PROMPTS,'intra_op_threads':2}


def normalize(values):
    return values/np.maximum(np.linalg.norm(values,axis=-1,keepdims=True),1e-12)


def preprocess(rgb,config):
    if rgb.ndim!=3 or rgb.shape[2]!=3 or rgb.dtype!=np.uint8:raise ValueError('需要uint8 RGB画面')
    h,w=rgb.shape[:2];bounds=detect_content_bounds(rgb)
    left,top,right,bottom=round(bounds[0]*w),round(bounds[1]*h),round(bounds[2]*w),round(bounds[3]*h)
    image=Image.fromarray(rgb[top:bottom,left:right]);w,h=image.size
    edge=config['size']['shortest_edge'];size=(edge,int(edge*h/w)) if w<h else (int(edge*w/h),edge)
    image=image.resize(size,Image.Resampling.BICUBIC)
    cw,ch=config['crop_size']['width'],config['crop_size']['height'];x,y=(size[0]-cw)//2,(size[1]-ch)//2
    pixels=np.asarray(image.crop((x,y,x+cw,y+ch)),dtype=np.float32)*np.float32(config['rescale_factor'])
    pixels=(pixels-np.asarray(config['image_mean'],np.float32))/np.asarray(config['image_std'],np.float32)
    return np.ascontiguousarray(pixels.transpose(2,0,1))


class VisualRecognizer:
    def __init__(self):
        import onnxruntime as ort
        from tokenizers import Tokenizer
        manifest=json.loads((DIRECTORY/'manifest.json').read_text(encoding='utf-8'))
        for name,info in manifest['files'].items():
            if hashlib.sha256(project_path(DIRECTORY/name).read_bytes()).hexdigest()!=info['sha256']:
                raise ValueError('视觉模型文件校验失败')
        self.version=manifest['revision'];self.labels=list(PROMPTS)
        self.preprocessor=json.loads((DIRECTORY/'preprocessor_config.json').read_text(encoding='utf-8'))
        options=ort.SessionOptions();options.intra_op_num_threads=2;options.inter_op_num_threads=1
        options.execution_mode=ort.ExecutionMode.ORT_SEQUENTIAL
        tokenizer=Tokenizer.from_file(str(DIRECTORY/'tokenizer.json'))
        tokenizer.enable_truncation(max_length=77)
        tokenizer.enable_padding(length=77,pad_id=tokenizer.token_to_id('<|endoftext|>'),pad_token='<|endoftext|>')
        texts=[template.format(description) for description in PROMPTS.values() for template in TEMPLATES]
        ids=np.asarray([item.ids for item in tokenizer.encode_batch(texts)],dtype=np.int64)
        text=ort.InferenceSession(str(DIRECTORY/'onnx/text_model_quantized.onnx'),options,providers=['CPUExecutionProvider'])
        encoded=text.run(['text_embeds'],{'input_ids':ids})[0]
        self.text_features=normalize(normalize(encoded).reshape(len(PROMPTS),len(TEMPLATES),-1).mean(axis=1))
        del text
        self.vision=ort.InferenceSession(str(DIRECTORY/'onnx/vision_model_quantized.onnx'),options,providers=['CPUExecutionProvider'])

    def embed(self,rgb):
        pixels=preprocess(rgb,self.preprocessor)[None]
        return normalize(self.vision.run(['image_embeds'],{'pixel_values':pixels})[0])[0]

    def classify(self,features):
        if not len(features):raise ValueError('没有实际图像特征')
        scores=self.text_features@normalize(np.mean(features,axis=0))
        order=np.argsort(scores)[::-1];first=int(order[0]);score=float(scores[first]);margin=score-float(scores[order[1]])
        accepted=score>=CONFIG['minimum_cosine'] and margin>=CONFIG['minimum_margin']
        label=self.labels[first]
        return {'action_id':label if accepted and not label.startswith('unknown_') else None,
                'top_label':label,'cosine_similarity':score,'margin':margin,'accepted':accepted,
                'reason':'background' if accepted and label.startswith('unknown_') else None if accepted else 'uncertain_visual_action',
                'top_three':[{'label':self.labels[int(i)],'cosine_similarity':float(scores[i])} for i in order[:3]],
                'score_is_calibrated_probability':False}
