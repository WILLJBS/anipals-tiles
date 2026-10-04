import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from cloud_collect_full import prepare, resume
from cloud_full_runtime import merge_caches
from cloud_collector_io import Journal
import test_cloud_full_contract as fixtures


class FullRuntimeTest(unittest.TestCase):
    setUp=fixtures.FullContractTest.setUp
    blob=fixtures.FullContractTest.blob
    proof=fixtures.FullContractTest.proof
    def test_prepare_cannot_accept_empty_pilot_or_minutes_out_of_bound(self):
        with tempfile.TemporaryDirectory() as tmp:
            for minutes,refs in [(301,[]),(1,[])]:
                with self.assertRaises(ValueError):
                    prepare(self.store,{'schema':'anipals-global-collector-request-v1','maxMinutes':minutes,'pilotResults':refs},Path(tmp))
    def test_prior_remains_in_budget_after_checkpoint_restart(self):
        j=Journal(self.store,'e'*64,1000,32); token=j.reserve(60); j.settle(token,15); j.snapshot()
        self.assertEqual(Journal(self.store,'e'*64,1000,32).used,47)
    def test_cache_merge_is_idempotent_without_duplicate_sequence_snapshot(self):
        j=Journal(self.store,'e'*64,1000,32)
        contract={'namespaceLedgers':[{'namespace':self.namespace}]}
        merge_caches(self.store,j,contract); merge_caches(self.store,j,contract)
        self.assertEqual(Journal(self.store,'e'*64,1000,32).used,32)
    def test_resume_refetches_actual_proof_and_rejects_budget_substitution(self):
        contract={'seed':self.seed_ref,'seedArchiveReceipt':self.receipt,'verifiedPilotProofs':[{'result':self.proof()['result']}],
                  'lineageSha256':'a','priorSourceBytes':32,'sourceBudgetBytes':1000,'collectorCode':self.spec['code'],
                  'appSourceSha':'b'*40,'roster':self.seed['roster'],'index':self.seed['index']}
        spec=self.spec|{'mode':'full','globalScopeCount':6222,'workers':1,'themes':['base','places'],'maxMinutes':1,
                       'contract':self.blob(contract)}
        with tempfile.TemporaryDirectory() as tmp,patch('cloud_collect_full.rebuild_contract',return_value=contract|{'priorSourceBytes':33}) as actual:
            with self.assertRaisesRegex(ValueError,'REVALIDATION'):
                resume(self.store,spec,'d'*64,Path(tmp),{'kind':'offline'})
            self.assertEqual(actual.call_count,1)


if __name__=='__main__':unittest.main()
