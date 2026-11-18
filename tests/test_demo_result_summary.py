import copy
import json
import unittest

from fitness_app.demo_result_summary import evidence_ranges,review_ranges,timecode
from fitness_app.paths import ROOT


class DemoResultTimeTests(unittest.TestCase):
    def test_actual_lateral_lean_evidence_is_not_notification_frames(self):
        manifest=json.loads((ROOT/'data/presentation-assets.json').read_text(encoding='utf-8'))
        demo=manifest['demos']['user-1688']
        track=json.loads((ROOT/demo['track']['path']).read_text(encoding='utf-8'))
        before=copy.deepcopy(track)
        issues=evidence_ranges(track,demo['duration_ms'])
        self.assertEqual([(r['start_ms'],r['end_ms']) for r in issues],[(5800,6200)])
        self.assertEqual(issues[0]['seek_ms'],5900)
        reviewed=review_ranges(demo)
        self.assertEqual([(r['start_ms'],r['end_ms']) for r in reviewed],[(2000,15433)])
        self.assertEqual(reviewed[0]['source'],'user_confirmed_demo_annotation')
        self.assertEqual(track,before)

    def test_trimmed_source_evidence_clamped_once_and_separate_episodes_kept(self):
        def row(stamp,start,end):
            return {'t':stamp,'cue':{'start_ms':start+5500,'end_ms':end+5500},
                'feedback':{'state':'issue','title':'侧倾','instruction':'坐正',
                    'checks':[{'code':'lean','status':'issue','side':'left'}],
                    'highlight':{'code':'lean','side':'left','evidence_time_base':'source_ms',
                                 'evidence_start_ms':start,'evidence_end_ms':end}}}
        track={'frames':[row(100,-700,100),row(200,-700,200),row(1600,1500,1600)]}
        result=evidence_ranges(track,1700)
        self.assertEqual([(r['start_ms'],r['end_ms']) for r in result],[(0,200),(1500,1600)])
        self.assertEqual([r['basis'] for r in result],['source_evidence','source_evidence'])

    def test_unconfirmed_or_out_of_video_review_is_not_published(self):
        base={'duration_ms':1000,'display_annotation':{'source':'user_confirmed_demo_annotation',
              'scope':'time_range','start_ms':100,'end_ms':900,'issues':['侧倾'],'instruction':'坐正'}}
        self.assertEqual(len(review_ranges(base)),1)
        for field,value in [('source','unknown'),('scope','whole_video'),('end_ms',1100),('start_ms',-1)]:
            changed=copy.deepcopy(base);changed['display_annotation'][field]=value
            self.assertEqual(review_ranges(changed),[])
        self.assertEqual(timecode(5800),'00:05.8')
        self.assertEqual(timecode(15433),'00:15.4')


if __name__=='__main__':unittest.main()
