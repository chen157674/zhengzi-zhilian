import unittest
import torch
from fitness_app.video_encoder_adaptation import configure,loss_terms,route_needed,apply_prediction,PREFIXES


class EncoderAdaptationTest(unittest.TestCase):
    def test_encoder_and_temporal_parameters_are_trainable_but_prefix_stays_frozen(self):
        class Tiny:
            def __init__(self):self.values=[(p+'weight',torch.nn.Parameter(torch.ones(1))) for p in PREFIXES]+[('encoder.vision_model.encoder.layers.10.weight',torch.nn.Parameter(torch.ones(1)))]
            def named_parameters(self):return iter(self.values)
        net=Tiny();selected=configure(net);self.assertEqual(len(selected),len(PREFIXES));self.assertFalse(net.values[-1][1].requires_grad)
        net.values.pop(0)
        with self.assertRaises(ValueError):configure(net)

    def test_allowed_family_is_not_forced_to_leaf_and_wrong_teacher_is_not_distilled(self):
        logits=torch.tensor([[0.,0.,2.],[1.,1.,0.]],requires_grad=True);mask=torch.tensor([[True,True,False],[True,True,False]])
        teacher=torch.tensor([[.01,.01,.98],[.9,.09,.01]]);ce,kl,protected=loss_terms(logits,mask,torch.ones(2),teacher)
        self.assertEqual(protected.tolist(),[False,True]);self.assertEqual(float(kl[0].detach()),0.)
        ce[0].backward();self.assertLess(float(logits.grad[0,0]),0.);self.assertAlmostEqual(float(logits.grad[0,0]),float(logits.grad[0,1]))

    def test_source_weights_are_not_cancelled_by_microbatch_normalization(self):
        logits=torch.zeros((1,2));mask=torch.tensor([[True,False]]);teacher=torch.tensor([[.5,.5]])
        low=loss_terms(logits,mask,torch.tensor([.2]),teacher)[0];high=loss_terms(logits,mask,torch.tensor([2.]),teacher)[0]
        self.assertAlmostEqual(float(high/low),10.,places=5)
        with self.assertRaises(ValueError):loss_terms(logits,mask,torch.ones(1),torch.tensor([[float('nan'),0.]]))

    def test_curl_rescue_still_requires_relevant_scope_confidence_and_positive_motion(self):
        base={'action_id':None,'top_action_id':'hammer_curl','score':.6,'motion_evidence':{'veto':False}}
        moving={'state':'moving','veto':False};self.assertTrue(route_needed(base));self.assertEqual(apply_prediction(base,'hammer_curl',.9,.7,moving,'test')['action_id'],'hammer_curl')
        for motion in ({'state':'insufficient','veto':False},{'state':'moving','veto':True}):self.assertEqual(apply_prediction(base,'hammer_curl',.9,.7,motion,'test'),base)
        self.assertEqual(apply_prediction(base,'hammer_curl',.79,.7,moving,'test'),base)
        self.assertEqual(apply_prediction(base,'bodyweight_squat',.99,.9,moving,'test'),base)
        self.assertFalse(route_needed(base|{'action_id':'standard_push_up'}))


if __name__=='__main__':unittest.main()
