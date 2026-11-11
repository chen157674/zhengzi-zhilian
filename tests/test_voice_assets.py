import copy
from pathlib import Path
import stat
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fitness_app.voice_assets import voice_catalogue
from fitness_app.presentation_assets import MANIFEST,_read


class VoiceAssetsTests(unittest.TestCase):
    def test_absent_user_files_stay_unavailable_with_exact_three_state_texts(self):
        result=voice_catalogue('bodyweight_squat');self.assertEqual(result['available_count'],0)
        self.assertEqual(set(result['prompts']),{'training.started','training.resting','tracking.waiting'})
        self.assertTrue(all(p['status']=='missing' and not p['url'] for p in result['prompts'].values()))

    def test_ambiguous_and_unreviewed_files_never_look_ready(self):
        original=Path.stat
        def fake_stat(p,*args,**kwargs):
            if 'static/audio/' in p.as_posix() and p.name in names:return SimpleNamespace(st_size=100,st_mtime_ns=1,st_mode=stat.S_IFREG)
            return original(p,*args,**kwargs)
        names={'training.started.mp3','training.started.wav'}
        with patch.object(Path,'stat',fake_stat):self.assertEqual(voice_catalogue('bodyweight_squat')['prompts']['training.started']['status'],'ambiguous_files')
        names={'training.started.mp3'}
        with patch.object(Path,'stat',fake_stat):self.assertEqual(voice_catalogue('bodyweight_squat')['prompts']['training.started']['status'],'review_required')

    def test_candidate_issue_cannot_become_enabled_by_adding_audio_metadata(self):
        st=MANIFEST.stat();data=copy.deepcopy(_read(st.st_mtime_ns,st.st_size))
        data['voice']['prompts']['squat.range']={'kind':'quality','priority':90,'text':'D-POSE candidate','audio_review':None}
        with patch('fitness_app.voice_assets._read',return_value=data):result=voice_catalogue('bodyweight_squat')
        self.assertEqual(result['prompts']['squat.range']['status'],'definition_unverified');self.assertEqual(result['available_count'],0)


if __name__=='__main__':unittest.main()
