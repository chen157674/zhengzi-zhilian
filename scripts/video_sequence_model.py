"""轻量视频双分支TCN；训练工具依赖PyTorch，正式服务只使用验收后的ONNX。"""
import torch
from torch import nn


class Residual(nn.Module):
    def __init__(self,dilation):
        super().__init__()
        self.layers=nn.Sequential(nn.Conv1d(96,96,3,padding=dilation,dilation=dilation),nn.GELU(),
                                  nn.Dropout(.15),nn.Conv1d(96,96,1))
    def forward(self,x):return torch.relu(x+self.layers(x))


class VideoSequenceModel(nn.Module):
    def __init__(self,classes,text_features=None,strengths=None):
        super().__init__()
        self.pose=nn.Sequential(nn.Conv1d(110,96,3,padding=1),nn.GELU(),Residual(2),Residual(4),Residual(8))
        self.image=nn.Sequential(nn.Linear(512,128),nn.GELU(),nn.Dropout(.3))
        self.output=nn.Sequential(nn.Linear(320,128),nn.GELU(),nn.Dropout(.3),nn.Linear(128,classes))
        self.register_buffer('text_features',torch.zeros(classes,512) if text_features is None else torch.as_tensor(text_features,dtype=torch.float32))
        self.register_buffer('strengths',torch.ones(classes) if strengths is None else torch.as_tensor(strengths,dtype=torch.float32))
    def forward(self,images,pose):
        joints=pose[:,:,:52].reshape(pose.shape[0],pose.shape[1],13,4)
        difference=joints[:,1:,:,:3]-joints[:,:-1,:,:3]
        both=joints[:,1:,:,3:4]*joints[:,:-1,:,3:4]
        delta_joints=torch.cat((difference*both,torch.zeros_like(both)),dim=-1).reshape(pose.shape[0],pose.shape[1]-1,52)
        delta_joints=torch.cat((delta_joints,torch.zeros_like(pose[:,1:,52:])),dim=-1)
        delta=torch.cat((torch.zeros_like(pose[:,:1]),delta_joints),dim=1)
        pose_features=self.pose(torch.cat((pose,delta),dim=-1).transpose(1,2))
        visible=(pose[:,:,3:52:4].mean(dim=-1)>.1).to(pose.dtype).unsqueeze(1)
        mean=(pose_features*visible).sum(dim=2)/visible.sum(dim=2).clamp(min=1)
        last=pose_features[:,:,-1]
        mask=images[:,:,512:513]
        image_mean=(images[:,:,:512]*mask).sum(dim=1)/mask.sum(dim=1).clamp(min=1)
        residual=self.output(torch.cat((self.image(image_mean),mean,last),dim=-1))
        # 稀缺类别保留冻结的CLIP语义先验，限制单一视频产生的大幅类别偏置。
        normalized=image_mean/image_mean.norm(dim=-1,keepdim=True).clamp(min=1e-12)
        prior=80*(normalized@self.text_features.T)
        return prior+4*torch.tanh(residual)*self.strengths
