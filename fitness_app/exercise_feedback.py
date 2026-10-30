"""读取现成PSLeon随机森林的数值参数；不导入或执行其仓库代码。"""
import io
import pickle
import numpy as np
from .pretrained_resources import repo_asset

ACTIONS={'barbell_bench_press':'benchpress','conventional_deadlift':'deadlift','bodyweight_squat':'squat'}


class _Stored:
    def __new__(cls,*args):
        value=object.__new__(cls);value.args=args;value.state={};return value

    def __init__(self,*args):self.args=args

    def __setstate__(self,state):
        if not isinstance(state,dict):raise ValueError('不支持的模型状态')
        self.state=state


class _NumbersOnlyUnpickler(pickle.Unpickler):
    """仅允许已审查的NumPy数组还原和无行为的估计器状态占位对象。"""
    def find_class(self,module,name):
        if (module,name) in {
            ('sklearn.pipeline','Pipeline'),('sklearn.preprocessing._data','StandardScaler'),
            ('sklearn.ensemble._forest','RandomForestClassifier'),
            ('sklearn.tree._classes','DecisionTreeClassifier'),('sklearn.tree._tree','Tree')}:
            return _Stored
        if module in ('numpy.core.multiarray','numpy._core.multiarray') and name in ('_reconstruct','scalar'):
            from numpy._core import multiarray
            return getattr(multiarray,name)
        if module=='numpy' and name in ('ndarray','dtype'):return getattr(np,name)
        raise pickle.UnpicklingError(f'禁止模型调用未允许的类型：{module}.{name}')


class ExerciseFeedbackModel:
    """冻结的StandardScaler→RandomForest推理，兼容上游旧sklearn树结构。"""
    def __init__(self,action):
        if action not in ACTIONS:raise ValueError('该上游模型不支持此动作')
        self.action=action;name=ACTIONS[action]
        path,self.resource=repo_asset('exercise-pose-feedback',f'models/{name}/{name}.pkl')
        stored=_NumbersOnlyUnpickler(io.BytesIO(path.read_bytes())).load()
        steps=stored.state.get('steps',[])
        if len(steps)!=2 or not all(isinstance(item[1],_Stored) for item in steps):raise ValueError('上游流水线结构改变')
        scaler,forest=[item[1].state for item in steps]
        self.mean=np.asarray(scaler['mean_'],dtype=np.float64);self.scale=np.asarray(scaler['scale_'],dtype=np.float64)
        self.classes=np.asarray(forest['classes_']);self.trees=[]
        if len(self.mean)!=132 or not np.isfinite(self.scale).all() or (self.scale<=0).any():raise ValueError('上游特征定义不符')
        for estimator in forest['estimators_']:
            tree=estimator.state['tree_'].state;nodes=tree['nodes'];values=np.asarray(tree['values'],dtype=np.float64)
            if values.ndim!=3 or values.shape[1]!=1 or values.shape[2]!=len(self.classes):raise ValueError('上游树输出定义不符')
            required={'left_child','right_child','feature','threshold'}
            if not required.issubset(nodes.dtype.names):raise ValueError('不支持的树节点结构')
            self.trees.append((nodes,values[:,0,:]))
        if not self.trees or len(self.trees)>1000:raise ValueError('上游树数量不符')

    def predict_array(self,features):
        x=np.asarray(features,dtype=np.float64)
        if x.ndim!=2 or x.shape[1]!=132 or len(x)>100000 or not np.isfinite(x).all():raise ValueError('模型输入须为有限132维坐标/可见性')
        # sklearn树预测在StandardScaler之后转float32。
        x=((x-self.mean)/self.scale).astype(np.float32)
        probabilities=np.zeros((len(x),len(self.classes)),dtype=np.float64)
        for nodes,values in self.trees:
            current=np.zeros(len(x),dtype=np.intp)
            for depth in range(len(nodes)):
                active=nodes['left_child'][current]!=-1
                if not active.any():break
                rows=np.flatnonzero(active);where=current[rows];feature=nodes['feature'][where]
                if (feature<0).any() or (feature>=132).any():raise ValueError('树特征越界')
                left=x[rows,feature]<=nodes['threshold'][where]
                current[rows]=np.where(left,nodes['left_child'][where],nodes['right_child'][where])
                if (current<0).any() or (current>=len(nodes)).any():raise ValueError('树子节点越界')
            else:raise ValueError('树存在循环或深度异常')
            scores=values[current];denominator=scores.sum(axis=1,keepdims=True)
            if (denominator<=0).any():raise ValueError('树概率为空')
            probabilities+=scores/denominator
        probabilities/=len(self.trees)
        return self.classes[probabilities.argmax(axis=1)],probabilities

    def observe(self,landmarks):
        if len(landmarks)!=33 or any(not isinstance(p,dict) or not all(k in p for k in ('x','y','z','visibility')) for p in landmarks):
            return {'status':'unassessable','reason':'required_parts_missing'}
        if any(p.get('valid') is False or p['visibility']<.5 for p in landmarks):
            return {'status':'unassessable','reason':'required_parts_not_visible'}
        values=[[p[k] for p in landmarks for k in ('x','y','z','visibility')]]
        labels,probabilities=self.predict_array(values);label=str(labels[0])
        return {'status':'candidate','source':'PSLeon24/AI_Exercise_Pose_Feedback','action_id':self.action,
            'label':label,'confidence':float(probabilities[0].max()),'class_probabilities':
            {str(name):float(value) for name,value in zip(self.classes,probabilities[0])},
            'model_sha256':self.resource['sha256'],'independent_accuracy_validated':False}
