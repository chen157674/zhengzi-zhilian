"""受限子类复核与冻结向量身份，合成输入不充当动作准确率证据。"""
import copy,hashlib,unittest
import numpy as np
from fitness_app.action_expert import CHEST,OTHER,labels_for,refine_chest,select_temporal_training
from scripts.frozen_fusion_matrix import checked_matrix


class ActionExpertTests(unittest.TestCase):
    def test_unknown_nonchest_and_other_prediction_cannot_be_rescued_or_modified(self):
        for action in (None,'hammer_curl','barbell_bent_over_row','forearm_plank'):
            base={'action_id':action,'model_version':'base','motion_evidence':{'veto':action is None}}
            self.assertEqual(refine_chest(base,'dumbbell_bench_press',.99,.9,'candidate'),base)
        base={'action_id':'pec_deck_fly'}
        self.assertEqual(refine_chest(base,OTHER,.99,.9,'candidate'),base)
        self.assertEqual(refine_chest(base,'pec_deck_fly',.99,.9,'candidate'),base)

    def test_only_confident_chest_to_chest_change_preserves_source_without_mutation(self):
        base={'action_id':'dumbbell_chest_fly','model_version':'base','decision_basis':'family','motion_evidence':{'veto':False}};before=copy.deepcopy(base)
        result=refine_chest(base,'dumbbell_bench_press',.9,.7,'candidate')
        self.assertEqual(base,before);self.assertEqual(result['action_id'],'dumbbell_bench_press');self.assertFalse(result['runtime_enabled'])
        self.assertEqual(result['expert_review']['base_action_id'],'dumbbell_chest_fly');self.assertEqual(result['motion_evidence'],base['motion_evidence'])

    def test_bad_scores_and_below_gate_do_not_override(self):
        base={'action_id':'pec_deck_fly'}
        for score,margin in ((float('nan'),.9),(.9,float('inf')),(True,.9),(.79,.7),(.99,.24),(1.1,.9),(.9,-.1)):
            self.assertEqual(refine_chest(base,'machine_chest_press',score,margin,'candidate'),base)

    def test_motion_veto_cannot_be_overridden_even_with_inconsistent_accepted_label(self):
        base={'action_id':'dumbbell_chest_fly','motion_evidence':{'veto':True}}
        self.assertEqual(refine_chest(base,'dumbbell_bench_press',.99,.9,'candidate'),base)

    def test_temporal_sampling_keeps_all_positives_and_mandatory_face_negatives_never_evaluation(self):
        rows=[]
        def row(key,action,group,digest,partition='train',mandatory=False):
            return {'id':key,'action_id':action,'group':group,'source_sha256':digest,'partition':partition,
                'label_scope':{'allowed_actions':[action]},'training_origin':'bounded_partial_view_training' if mandatory else 'retained'}
        rows.extend([row('p1','dumbbell_bench_press','p','same'),row('p2','dumbbell_bench_press','p','same')])
        rows.extend([row('n'+str(i),'hammer_curl','g'+str(i//2),'s'+str(i)) for i in range(6)])
        rows.extend([row('face-a','unknown','g-new','face',mandatory=True),row('face-b','unknown','g-new','face',mandatory=True)])
        rows.append(row('never','dumbbell_bench_press','validation','v','validation'));before=copy.deepcopy(rows)
        selected=select_temporal_training(rows,2);chosen=[rows[i] for i in selected]
        self.assertTrue({'p1','p2','face-a','face-b'}<={r['id'] for r in chosen});self.assertNotIn('never',{r['id'] for r in chosen})
        negatives=[r for r in chosen if r['action_id']=='hammer_curl'];self.assertEqual(len(negatives),2);self.assertEqual(len({r['group'] for r in negatives}),2)
        self.assertEqual(selected,select_temporal_training(rows,2));self.assertEqual(rows,before)

    def test_family_supervision_stays_ambiguous_and_nonchest_maps_to_single_other(self):
        source={'label_scope':{'allowed_actions':['dumbbell_bench_press','incline_dumbbell_press']},'id':'not-a-feature'}
        self.assertEqual(labels_for(source),['dumbbell_bench_press','incline_dumbbell_press'])
        self.assertEqual(labels_for({'label_scope':{'allowed_actions':['standing_dumbbell_curl','hammer_curl']}}),[OTHER])

    def test_matrix_corruption_reordering_missing_or_nonfinite_rows_never_pass(self):
        matrix=np.zeros((2,2736),np.float32);matrix[0,0]=1.;matrix[1,1]=1.
        identities=[{'feature_sha256':hashlib.sha256(r.tobytes()).hexdigest()} for r in matrix]
        np.testing.assert_array_equal(checked_matrix(matrix,identities),matrix)
        for changed in (matrix[::-1].copy(),matrix[:1],matrix.astype(np.float64),matrix*np.nan):
            with self.assertRaises(ValueError):checked_matrix(changed,identities)


if __name__=='__main__':unittest.main()
