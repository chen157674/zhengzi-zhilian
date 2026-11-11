"""补检必须同帧同侧且双裁剪一致；合成点仅验证边界。"""
import copy,unittest
import numpy as np
from fitness_app.rtmpose_input import recover_wrist_hands
from test_movement_guidance import frame

class HandRecoveryTests(unittest.TestCase):
    def inputs(self,bend=60):
        f=frame(0,bend=bend);return f,np.zeros((1000,1000,3),np.uint8)

    def pose(self,f,mode=None):
        hands=f['pretrained']['hands'];calls=[]
        class Pose:
            def detect(self,rgb,bbox):
                calls.append(bbox)
                center=np.array([(bbox[0]+bbox[2])/2,(bbox[1]+bbox[3])/2])/1000
                hand=min(hands,key=lambda h:np.linalg.norm(center-np.array([h['keypoints'][0]['x'],h['keypoints'][0]['y']])))
                points=copy.deepcopy(hand['keypoints'])
                if mode=='low':points[0]['score']=.2
                if mode=='unstable':points[9]['x']+=(len(calls)%3)*.1
                return {'keypoints':points,'bbox':bbox,'model_sha256':'fixture'}
        return Pose(),calls

    def test_missing_detector_recovers_both_sides_without_changing_body(self):
        f,rgb=self.inputs();body=f['pretrained']['body'];before=copy.deepcopy(body);pose,calls=self.pose(f)
        hands,detail=recover_wrist_hands(body,[],pose,rgb)
        self.assertEqual(detail['recovered'],2);self.assertEqual(len(calls),4)
        self.assertEqual({h['body_wrist_index'] for h in hands},{9,10});self.assertEqual(body,before)
        self.assertTrue(all(h['crop_agreement_count']==2 for h in hands))
        self.assertTrue(all(not h['observation']['contact_with_handle_verified'] for h in hands))

    def test_good_existing_hand_skips_extra_inference(self):
        f,rgb=self.inputs();pose,calls=self.pose(f)
        hands,detail=recover_wrist_hands(f['pretrained']['body'],f['pretrained']['hands'],pose,rgb)
        self.assertEqual(detail['attempted'],0);self.assertEqual(calls,[]);self.assertEqual(len(hands),2)

    def test_low_response_or_crop_disagreement_cannot_recover(self):
        for mode in ('low','unstable'):
            f,rgb=self.inputs();pose,_=self.pose(f,mode)
            hands,detail=recover_wrist_hands(f['pretrained']['body'],[],pose,rgb)
            self.assertEqual(hands,[],mode);self.assertEqual(detail['recovered'],0)

    def test_failed_first_crop_can_be_recovered_only_by_two_other_agreeing_crops(self):
        f,rgb=self.inputs();base,calls=self.pose(f)
        class Retry:
            def detect(self,rgb,bbox):
                result=base.detect(rgb,bbox)
                if len(calls)%3==1:result['keypoints'][0]['score']=.2
                return result
        hands,detail=recover_wrist_hands(f['pretrained']['body'],[],Retry(),rgb)
        self.assertEqual(detail['recovered'],2);self.assertEqual(len(calls),6)
        self.assertTrue(all(h['crop_agreement_count']==2 for h in hands))

    def test_hidden_core_and_crossed_wrists_do_not_guess_hand_identity(self):
        f,rgb=self.inputs();pose,_=self.pose(f);f['pretrained']['body']['keypoints'][5]['score']=.1
        self.assertEqual(recover_wrist_hands(f['pretrained']['body'],[],pose,rgb)[0],[])
        f,rgb=self.inputs();pose,_=self.pose(f)
        f['pretrained']['body']['keypoints'][10]=copy.deepcopy(f['pretrained']['body']['keypoints'][9])
        self.assertEqual(recover_wrist_hands(f['pretrained']['body'],[],pose,rgb)[0],[])

    def test_recovered_palm_reaches_sustained_wrist_observer_and_retracts(self):
        from fitness_app.movement_guidance import MovementGuidance
        observer=MovementGuidance('machine_chest_press','fixture')
        for stamp in range(0,1800,200):
            f,rgb=self.inputs();f['captured_ms']=stamp;f['pretrained']['timestamp_ms']=stamp;pose,_=self.pose(f)
            f['pretrained']['hands'],_=recover_wrist_hands(f['pretrained']['body'],[],pose,rgb)
            items,_=observer.update(f,{})
        self.assertEqual({i.get('side') for i in items if i['code'].endswith('wrist_bend') and i['status']=='issue'},{'left','right'})
        f['captured_ms']+=200;f['pretrained']['timestamp_ms']=f['captured_ms'];f['pretrained']['hands']=[]
        items,_=observer.update(f,{})
        self.assertTrue(all(i['status']=='unassessable' for i in items if i['code'].endswith('wrist_bend')))

if __name__=='__main__':unittest.main()
