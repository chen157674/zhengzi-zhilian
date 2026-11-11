"""整段人物隔离、数值特征鲁棒性与不受信任pickle边界。"""
import io,pickle,unittest
import numpy as np
from fitness_app.ec3d_training import NumericReader,sequences,sequence_features,metrics,SPLIT

class EC3DTrainingTests(unittest.TestCase):
    def test_pickle_cannot_resolve_code_or_process_globals(self):
        with self.assertRaises(pickle.UnpicklingError):NumericReader(io.BytesIO(pickle.dumps(eval))).load()

    def test_distance_features_are_rigid_transform_and_scale_invariant(self):
        poses=np.random.default_rng(1).normal(size=(30,3,19))
        rotation=np.array([[0.,-1.,0.],[1.,0.,0.],[0.,0.,1.]])
        changed=np.einsum('ij,tjk->tik',rotation,poses)*2.5+np.array([.3,-.7,.2])[None,:,None]
        np.testing.assert_allclose(sequence_features(poses),sequence_features(changed),atol=1e-12)

    def test_sequence_never_mixes_people_and_unknown_label_is_not_error_truth(self):
        labels=[]
        for subject,label in [('Hugues','1'),('Sena','3'),('Vidit','1'),('Sena','10')]:
            labels.extend([['SQUAT',subject,label,'1',str(i)] for i in range(8)])
        poses=np.random.default_rng(2).normal(size=(len(labels),3,25))
        result,excluded=sequences(np.asarray(labels),poses)
        self.assertEqual(len(result),3);self.assertEqual(len(excluded),1)
        self.assertEqual({r['partition']:r['subject'] for r,_ in result},{'train':'Hugues','validation':'Sena','test':'Vidit'})
        for a,b in [('train','test'),('train','validation'),('validation','test')]:self.assertFalse(set(SPLIT[a])&set(SPLIT[b]))

    def test_duplicate_frame_identity_and_unseen_subject_fail_closed(self):
        poses=np.ones((8,3,25));labels=np.array([['Plank','Vidit','1','1','0']]*8)
        with self.assertRaises(ValueError):sequences(labels,poses)
        labels[:,1]='Other'
        with self.assertRaises(ValueError):sequences(labels,poses)

    def test_error_detection_and_exact_error_type_are_separate(self):
        m=metrics([1,3,4,4],[3,4,4,1])
        self.assertEqual(m['accuracy'],.25)
        self.assertEqual(m['error_detection']['tp'],2);self.assertEqual(m['error_detection']['fn'],1)
        self.assertEqual(m['error_detection']['fp'],1)

    def test_invalid_or_degenerate_skeleton_is_rejected(self):
        for value in (np.zeros((10,3,19)),np.full((10,3,19),np.nan),np.ones((7,3,19)),np.ones((10,3,25))):
            with self.assertRaises(ValueError):sequence_features(value)

if __name__=='__main__':unittest.main()
