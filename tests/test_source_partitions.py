"""新来源预留约束跨ID/作者/原片生效，不用真实保留视频运行模型。"""
import unittest
from fitness_app.source_partitions import enforce_source_policy,validate_prospective_role,prospective_holdout_policies


class SourcePartitionTests(unittest.TestCase):
    def policy(self,reserved=True):
        return {'id':'youtube-reserved','sha256':'source','source_group':'channel:reserved','evaluation_only':True,
            'partition':'reserved_holdout' if reserved else 'validation','model_inference_allowed_before_freeze':not reserved}
    def test_reserved_identity_creator_or_digest_cannot_enter_training_under_an_alias(self):
        for identity in ({'source_id':'youtube-reserved'},{'group':'channel:reserved'},{'source_sha256':'source'}):
            with self.subTest(identity=identity),self.assertRaises(ValueError):enforce_source_policy([{'id':'alias','partition':'train',**identity}],[self.policy()])
    def test_reserved_inference_denied_even_when_entry_claims_diagnostic(self):
        with self.assertRaises(ValueError):enforce_source_policy([{'source_id':'youtube-reserved','partition':'diagnostic'}],[self.policy()],operation='inference')
    def test_normal_evaluation_source_can_be_evaluated_but_not_trained(self):
        row={'source_id':'youtube-reserved','partition':'validation'};policy=self.policy(False)
        enforce_source_policy([row],[policy]);enforce_source_policy([row],[policy],operation='inference')
        with self.assertRaises(ValueError):enforce_source_policy([{**row,'partition':'train'}],[policy])
    def test_other_training_sources_are_unchanged_and_invalid_operation_refuses(self):
        row={'id':'other','group':'channel:other','source_sha256':'other','partition':'train'}
        enforce_source_policy([row],[self.policy()]);enforce_source_policy([row],[self.policy()],operation='inference')
        with self.assertRaises(ValueError):enforce_source_policy([row],[self.policy()],operation='override')
    def test_asset_provenance_restriction_does_not_block_unrelated_creator_assets(self):
        policy={**self.policy(),'restriction_scope':'asset'}
        other={'id':'other','source_id':'other','source_sha256':'other','group':'channel:reserved','partition':'train'}
        enforce_source_policy([other],[policy]);enforce_source_policy([other],[policy],operation='inference')
        with self.assertRaises(ValueError):enforce_source_policy([{**other,'source_sha256':'source'}],[policy])
    def test_unconfirmed_equipment_asset_is_excluded_from_training_but_can_be_diagnosed(self):
        policy={**self.policy(False),'evaluation_only':False,'exclude_from_training':True,'restriction_scope':'asset'}
        row={'source_id':'youtube-reserved','partition':'train'}
        with self.assertRaises(ValueError):enforce_source_policy([row],[policy])
        enforce_source_policy([{**row,'partition':'diagnostic'}],[policy],operation='inference')
    def test_duplicate_alias_exclusion_keeps_legal_canonical_media_and_other_publisher_assets(self):
        policy={'id':'duplicate','duplicate_of':'canonical','exclude_from_training':True,'partition':'train','source_group':'publisher','sha256':'same'}
        original={'id':'original-window','source_id':'canonical','source_sha256':'same','group':'publisher','partition':'train'}
        enforce_source_policy([original],[policy]);enforce_source_policy([{**original,'source_id':'other','source_sha256':'other'}],[policy])
        for changed in ({'source_id':'duplicate'},{'source_id':None,'id':'duplicate-1000-5000'}):
            with self.assertRaises(ValueError):enforce_source_policy([{**original,**changed}],[policy])
    def test_new_and_same_role_publishers_keep_declared_partition(self):
        for role in ('train','validation_supplement','reserved_new_publisher_holdout'):validate_prospective_role('new',role,{})
        validate_prospective_role('known','train',{'known':{'train'}})
        validate_prospective_role('known','validation_supplement',{'known':{'validation','diagnostic'}})
        validate_prospective_role('known','reserved_new_publisher_holdout',{'known':{'reserved_holdout'}})
    def test_old_evaluation_or_reserved_publisher_cannot_become_new_training(self):
        for existing,role in [({'validation'},'train'),({'diagnostic'},'train'),({'reserved_holdout'},'train'),({'train'},'validation_supplement'),({'reserved_holdout'},'validation_supplement'),({'train'},'reserved_new_publisher_holdout'),({'validation'},'reserved_new_publisher_holdout')]:
            with self.subTest(existing=existing,role=role),self.assertRaises(ValueError):validate_prospective_role('known',role,{'known':existing})
    def test_unseen_validation_asset_is_frozen_without_blocking_old_author_evaluation(self):
        policy={**self.policy(False),'model_inference_allowed_before_freeze':False,'inference_restriction_scope':'asset'}
        other={'id':'old-validation-window','source_id':'old-video','source_sha256':'old','group':'channel:reserved','partition':'validation'}
        enforce_source_policy([other],[policy],operation='inference')
        with self.assertRaises(ValueError):enforce_source_policy([other|{'partition':'train'}],[policy])
        for identity in ({'source_id':'youtube-reserved'},{'source_sha256':'source'}):
            with self.subTest(identity=identity),self.assertRaises(ValueError):enforce_source_policy([other|identity],[policy],operation='inference')
    def test_confirmed_holdout_remains_protected_after_transport_failure_without_fake_media(self):
        row={'id':'planned','group':'channel:planned','prospective_role':'reserved_new_publisher_holdout','primary_cc_verified':True,
             'partition_review_before_media_download':True,'model_inference_allowed_before_freeze':False,'downloaded':False,'skip_download':True,'download_status':'transport_failed'}
        policies=prospective_holdout_policies({'videos':[row]});self.assertEqual(len(policies),1);self.assertTrue(policies[0]['policy_only']);self.assertNotIn('path',policies[0])
        alias={'id':'different-video','group':'channel:planned','partition':'train','source_sha256':'different'}
        with self.assertRaises(ValueError):enforce_source_policy([alias],policies)
        with self.assertRaises(ValueError):enforce_source_policy([alias|{'partition':'diagnostic'}],policies,operation='inference')
        for change in ({'primary_cc_verified':False},{'partition_review_before_media_download':False},{'prospective_role':'validation_supplement'}):self.assertEqual(prospective_holdout_policies({'videos':[row|change]}),[])


if __name__=='__main__':unittest.main()
