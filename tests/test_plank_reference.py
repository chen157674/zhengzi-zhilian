"""原平板权重数值与可见性拒判；原CSV不作为独立人物精度。"""
import csv,io,pickle,unittest
import numpy as np
from fitness_app.plank_reference import PlankReference,INDICES,_Reader,DIRECTORY


class PlankReferenceTests(unittest.TestCase):
    def test_original_coordinate_probabilities_match_current_numeric_sklearn(self):
        from sklearn.linear_model import LogisticRegression
        model=PlankReference()
        with (DIRECTORY/'test.csv').open(encoding='utf-8-sig',newline='') as stream:
            reader=csv.DictReader(stream);columns=[c for c in reader.fieldnames if c!='label'];rows=list(reader)
        self.assertEqual(columns,model.feature_names)
        values=np.asarray([[float(row[c]) for c in columns] for row in rows])
        labels,scores=model.predict_array(values)
        reference=LogisticRegression();reference.classes_=model.classes;reference.coef_=model.coef
        reference.intercept_=model.intercept;reference.n_features_in_=68
        expected=reference.predict_proba((values-model.mean)/model.scale)
        np.testing.assert_array_equal(scores,expected)
        self.assertEqual(scores.shape,(710,3));self.assertTrue(np.isfinite(scores).all())
        self.assertTrue(set(labels)<={0,1,2})

    def test_low_visibility_and_two_dimensional_substitutes_are_refused(self):
        model=PlankReference()
        with (DIRECTORY/'test.csv').open(encoding='utf-8-sig',newline='') as stream:row=next(csv.DictReader(stream))
        landmarks=[{'x':.5,'y':.5,'z':0.,'visibility':.9,'presence':.9} for _ in range(33)]
        names=('nose','left_shoulder','right_shoulder','left_elbow','right_elbow','left_wrist','right_wrist',
               'left_hip','right_hip','left_knee','right_knee','left_ankle','right_ankle','left_heel','right_heel','left_foot_index','right_foot_index')
        for index,name in zip(INDICES,names):
            landmarks[index].update({key:float(row[name+'_'+suffix]) for key,suffix in (('x','x'),('y','y'),('z','z'),('visibility','v'))})
        landmarks[13]['visibility']=.1
        self.assertEqual(model.observe(landmarks)['reason'],'required_parts_not_visible')
        for point in landmarks:point.update(visibility=.9,depth_available=False)
        self.assertEqual(model.observe(landmarks)['status'],'unassessable')
        self.assertFalse(model.manifest['runtime_enabled'])

    def test_unapproved_pickle_behavior_and_invalid_arrays_are_refused(self):
        with self.assertRaises(pickle.UnpicklingError):_Reader(io.BytesIO(pickle.dumps(eval))).load()
        model=PlankReference()
        for value in (np.zeros((1,67)),np.full((1,68),np.nan),np.zeros((0,68))):
            with self.assertRaises(ValueError):model.predict_array(value)


if __name__=='__main__':unittest.main()
