"""离线候选的标签范围/泄漏/损失检查，不等同于真实动作准确率。"""
import unittest
import numpy as np
from scripts.partial_action_training import assert_source_split,source_weights,allowed_mask,partial_loss,fit_linear,probabilities,metrics


class PartialActionTrainingTests(unittest.TestCase):
    def row(self,key='a',action='normal',allowed=None,group='g',digest='h',partition='train'):
        actions=allowed or [action]
        return {'id':key,'action_id':action,'control_label':action,'group':group,'source_sha256':digest,'partition':partition,
                'label_scope':{'specificity':'family' if len(actions)>1 else 'leaf','allowed_actions':actions,'leaf_label':None if len(actions)>1 else action}}

    def test_family_loss_only_rewards_allowed_mass_and_stays_finite_at_extremes(self):
        import torch
        mask=torch.tensor([[True,True,False]]);w=torch.ones(1,dtype=torch.float64)
        values=[]
        for first,second in ((.7,.2),(.2,.7)):
            logits=torch.tensor(np.log([[first,second,.1]]),requires_grad=True);loss=partial_loss(logits,mask,w)
            loss.backward();self.assertTrue(torch.isfinite(logits.grad).all());values.append(float(loss.detach()))
        self.assertAlmostEqual(values[0],values[1]);self.assertAlmostEqual(values[0],-np.log(.9))
        logits=torch.tensor([[1000.,-1000.,0.]],dtype=torch.float64,requires_grad=True)
        loss=partial_loss(logits,torch.tensor([[False,True,False]]),w);loss.backward()
        self.assertTrue(torch.isfinite(loss));self.assertTrue(torch.isfinite(logits.grad).all())
        with self.assertRaises(ValueError):partial_loss(logits,torch.zeros((1,3),dtype=torch.bool),w)

    def test_singleton_is_exact_cross_entropy_and_uniform_leaf_split_is_not_used(self):
        import torch
        logits=torch.tensor([[1.,2.,3.],[3.,1.,-1.]],dtype=torch.float64);weights=torch.tensor([1.,2.],dtype=torch.float64)
        mask=torch.tensor([[False,True,False],[True,False,False]])
        expected=(torch.nn.functional.cross_entropy(logits,torch.tensor([1,0]),reduction='none')*weights).sum()/weights.sum()
        self.assertAlmostEqual(float(partial_loss(logits,mask,weights)),float(expected))
        rows=[self.row(allowed=['normal','hammer'])]
        np.testing.assert_array_equal(allowed_mask(rows,['normal','hammer','unknown']),[[True,True,False]])
        np.testing.assert_array_equal(allowed_mask(rows,['normal','hammer','unknown'],False),[[True,False,False]])

    def test_source_weights_ignore_duplicate_crops_and_equalize_groups(self):
        rows=[self.row(key=str(i),group=g,digest=h) for i,(g,h) in enumerate([('a','x'),('a','x'),('b','y'),('b','z')])]
        rows.append(self.row(key='other',action='hammer',group='c',digest='q'))
        weights=source_weights(rows);self.assertAlmostEqual(weights.sum(),len(rows))
        self.assertAlmostEqual(sum(weights[:2]),sum(weights[2:4]));self.assertAlmostEqual(sum(weights[:4]),weights[4])

    def test_source_split_rejects_hash_author_and_evaluation_only_leakage(self):
        train=[self.row()];evaluation=[self.row(key='v',group='other',digest='other',partition='diagnostic')]
        assert_source_split(train,evaluation)
        for change in ({'group':'g'},{'source_sha256':'h'}):
            with self.assertRaises(ValueError):assert_source_split(train,[{**evaluation[0],**change}])
        for change in ({'evaluation_only':True},{'exclude_from_training':True},{'partition':'diagnostic'}):
            with self.assertRaises(ValueError):assert_source_split([{**train[0],**change}],evaluation)

    def test_metrics_do_not_count_family_compatibility_as_confirmed_leaf(self):
        rows=[self.row(allowed=['normal','hammer'],partition='validation'),self.row(key='h',action='hammer',partition='validation'),self.row(key='n',action='unknown',partition='validation')]
        result=metrics(rows,np.asarray([[.1,.85,.05],[.4,.3,.3],[.1,.8,.1]]),['normal','hammer','unknown'])
        self.assertEqual(result['legacy_correct'],0);self.assertEqual(result['typed_compatible_correct'],1)
        self.assertEqual(result['per_class']['normal']['verified_leaf_clips'],0);self.assertIsNone(result['per_class']['normal']['verified_leaf_recall'])
        self.assertEqual(result['per_class']['hammer']['verified_leaf_recall'],0);self.assertEqual(result['negative_false_accepts'],1)
        rejected=metrics(rows[:1],np.asarray([[.34,.33,.33]]),['normal','hammer','unknown'])
        self.assertIsNone(rejected['predictions'][0]['accepted']);self.assertEqual(rejected['positive_clips'],1)

    def test_fit_is_deterministic_cpu_and_generalizes_on_separable_toy_inputs(self):
        x=np.asarray([[-2.],[-1.],[1.],[2.]])
        mask=np.asarray([[True,False],[True,False],[False,True],[False,True]])
        weights=np.ones(4);first=fit_linear(x,mask,weights);second=fit_linear(x,mask,weights)
        np.testing.assert_allclose(first['coef'],second['coef']);self.assertTrue(first['optimization']['stationarity_passed'])
        self.assertEqual(probabilities(np.asarray([[-1.5],[1.5]]),first).argmax(1).tolist(),[0,1])

    def test_missing_prediction_or_invalid_probability_cannot_drop_denominators(self):
        rows=[self.row(),self.row(key='b')]
        for value in ([[.9,.1]],[[.9,.1],[float('nan'),.2]],[[.9,.1],[.9,.2]]):
            with self.assertRaises(ValueError):metrics(rows,np.asarray(value),['normal','hammer'])


if __name__=='__main__':unittest.main()
