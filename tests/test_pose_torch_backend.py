"""可选GPU后端的结构错误／同帧CPU回退；不以这些替身验证姿态精度。"""
import os
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch,MagicMock
import numpy as np
from fitness_app.pose_torch_backend import TorchPoseSession,optional_cuda_session


class BackendTests(unittest.TestCase):
    def session(self,message):
        class Network:
            moved=None
            def __call__(self,value):raise RuntimeError(message)
            def to(self,device):self.moved=device
        self.cpu_calls=[]
        class CPU:
            def run(inner,names,feeds):self.cpu_calls.append(feeds);return [np.array([9.],np.float32)]
        session=TorchPoseSession.__new__(TorchPoseSession)
        session.lock=threading.RLock();session.network=Network();session.cpu_factory=CPU;session.cpu=None
        session.inputs=[SimpleNamespace(name='input',shape=[1,3,2,2])]
        session.execution_backend='torch_cuda_fp32'
        return session

    def test_cpu_parallelism_is_bounded_and_explicit_choice_validated(self):
        from fitness_app.rtmpose_input import cpu_thread_count
        with patch.dict(os.environ,{},clear=True):
            for cores,expected in ((None,1),(2,1),(8,2),(16,4),(64,4)):
                with patch('os.cpu_count',return_value=cores):self.assertEqual(cpu_thread_count(),expected)
        for value in ('1','2','4','8'):
            with patch.dict(os.environ,{'FITNESS_POSE_CPU_THREADS':value}):self.assertEqual(cpu_thread_count(),int(value))
        for value in ('0','9','four','2.5',''):
            with patch.dict(os.environ,{'FITNESS_POSE_CPU_THREADS':value}),self.assertRaises(ValueError):cpu_thread_count()

    def test_cpu_preference_and_unrelated_model_do_not_load_a_cuda_graph(self):
        with patch.dict(os.environ,{'FITNESS_POSE_DEVICE':'cpu'}):
            self.assertIsNone(optional_cuda_session('unused','rtmpose-body',None))
        with patch.dict(os.environ,{'FITNESS_POSE_DEVICE':'cuda'}):
            self.assertIsNone(optional_cuda_session('unused','rtmdet-hand',None))

    def test_realtime_auto_isolates_pose_on_cpu_without_changing_explicit_or_offline_choice(self):
        from fitness_app.gym_pose import GymPoseService
        with patch('fitness_app.gym_pose.ContentPoseService.__init__'),\
                patch('fitness_app.pretrained_policy.body_enabled',return_value=True),\
                patch('fitness_app.pretrained_policy.enabled',return_value=False),\
                patch('fitness_app.movement_guidance_policy.enabled',return_value=True),\
                patch('fitness_app.pretrained_input.PretrainedFrameModel') as model:
            for preference,realtime,expected in (('auto',True,'cpu'),('auto',False,None),
                    ('cpu',True,None),('cuda',True,None)):
                with self.subTest(preference=preference,realtime=realtime),patch.dict(os.environ,{'FITNESS_POSE_DEVICE':preference}):
                    GymPoseService(video=True,realtime=realtime)
                    model.assert_called_with(include_hands=True,device=expected)

    def test_session_cpu_override_does_not_construct_cuda_or_mutate_environment(self):
        from fitness_app.rtmpose_input import _session
        cpu=MagicMock();cpu.get_inputs.return_value=[SimpleNamespace(shape=[1,3,256,192])]
        with patch.dict(os.environ,{'FITNESS_POSE_DEVICE':'auto'}),\
                patch('fitness_app.rtmpose_input.onnx_asset',return_value=('unused',{})),\
                patch('onnxruntime.InferenceSession',return_value=cpu),\
                patch('fitness_app.pose_torch_backend.optional_cuda_session') as cuda:
            actual,record=_session('rtmpose-body',device='cpu')
            self.assertIs(actual,cpu);cuda.assert_not_called()
            self.assertEqual(os.environ['FITNESS_POSE_DEVICE'],'auto')

    def test_cpu_threads_are_passed_to_session_and_frozen_in_input_policy(self):
        from fitness_app.rtmpose_input import _session
        from fitness_app.pose_input import input_policy_snapshot
        from fitness_app.gym_pose import GymPoseService
        cpu=MagicMock();cpu.get_inputs.return_value=[SimpleNamespace(shape=[1,3,256,192])]
        with patch.dict(os.environ,{'FITNESS_POSE_DEVICE':'cpu','FITNESS_POSE_CPU_THREADS':'4',
                'FITNESS_BODY_LEAN':'1','FITNESS_MOVEMENT_GUIDANCE':'1'}),\
                patch('fitness_app.rtmpose_input.onnx_asset',return_value=('unused',{})),\
                patch('onnxruntime.InferenceSession',return_value=cpu) as session:
            _session('rtmpose-body',device='cpu')
            self.assertEqual(session.call_args.kwargs['sess_options'].intra_op_num_threads,4)
            previous=input_policy_snapshot(GymPoseService)
            self.assertEqual(previous['pretrained_cpu_threads'],4)
            os.environ['FITNESS_POSE_CPU_THREADS']='2'
            current=input_policy_snapshot(GymPoseService)
            self.assertEqual(current['pretrained_cpu_threads'],2)
            self.assertEqual(previous['pretrained_cpu_threads'],4)

    def test_cuda_resource_failure_retries_the_identical_tensor_on_cpu(self):
        import torch
        session=self.session('CUDA out of memory');feeds={'input':np.ones((1,3,2,2),np.float32)}
        before=(torch.backends.cuda.matmul.allow_tf32,torch.backends.cudnn.allow_tf32)
        with patch('torch.as_tensor',return_value=torch.ones(1)),patch('torch.cuda.empty_cache'):
            output=session.run(None,feeds)
        self.assertEqual(output[0].tolist(),[9.])
        self.assertIs(self.cpu_calls[0],feeds)
        self.assertEqual(session.execution_backend,'onnxruntime_cpu_fallback')
        self.assertEqual(before,(torch.backends.cuda.matmul.allow_tf32,torch.backends.cudnn.allow_tf32))

    def test_pose_uses_its_own_stream_without_waiting_for_the_default_visual_stream_each_frame(self):
        import torch
        session=self.session('CUDA out of memory');session.cuda_stream=object()
        context=MagicMock()
        with patch('torch.cuda.stream',return_value=context) as streams,patch('torch.as_tensor',return_value=torch.ones(1)),patch('torch.cuda.empty_cache'):
            session.run(None,{'input':np.ones((1,3,2,2),np.float32)})
        streams.assert_called_once();context.__enter__.assert_called_once()
        self.assertEqual(session.execution_backend,'onnxruntime_cpu_fallback')

    def test_shape_or_unrelated_model_bug_is_not_hidden_as_a_device_fallback(self):
        import torch
        session=self.session('model shape mismatch')
        with self.assertRaises(ValueError):session.run(None,{'input':np.ones((2,3,2,2),np.float32)})
        with patch('torch.as_tensor',return_value=torch.ones(1)),self.assertRaises(RuntimeError):
            session.run(None,{'input':np.ones((1,3,2,2),np.float32)})
        self.assertEqual(self.cpu_calls,[])


if __name__=='__main__':unittest.main()
