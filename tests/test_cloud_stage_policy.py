import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from test_cloud_collector import S3, upload
from cloud_collector_io import PrivateStore, Journal
from cloud_full_stage import before_stage, run_stage, finalize, stage_context, initial_progress
from cloud_stage_policy import load_result, checkpoint, continuation, complete_result
from cloud_full_runtime import remaining_seconds
from cloud_collect_places import Deadline
from cloud_collect_full import WORKFLOW
from cloud_execution import REPOSITORY


class StagePolicyTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.store = PrivateStore(S3(), 'offline', upload); self.sha = 'a'*64
        self.roster = self.blob({'cities': list(range(6222))})
        self.spec = dict(sourceBudgetBytes=1000, priorSourceBytes=10, appSourceSha='b'*40,
                         roster=self.roster, index=self.blob({'index':True}))
        self.spec['contract'] = self.blob({'seed': self.blob({'sourceFilesTotal':80}), 'lineageSha256':'e'*64})
        self.current = dict(kind='github-actions', repository=REPOSITORY, workflowPath=WORKFLOW,
            runId=200, runAttempt=1, runnerSourceSha='c'*40, workflowSha='c'*40, stageIndex=1, jobKey='stage1')
        self.proofs = []
    def blob(self, value):
        return self.store.save_json('archive/sha256/', value)
    def journal(self):
        return Journal(self.store, self.sha, 1000, 10)
    def result(self, context=None, failure='Deadline', complete=False, progress=True):
        j=self.journal()
        if progress:
            token=j.reserve(3);j.settle(token,1,{'cacheKey':str(j.seq),'blob':{}})
        state=j.snapshot()
        value=dict(schema='anipals-cloud-collector-result-v1', complete=complete, specSha256=self.sha,
            execution=context or dict(self.current,runId=100,runnerSourceSha='d'*40,workflowSha='d'*40),
            sourceScopeCount=6222, publication='pending', navigationTargetsPublished=0,
            sourceBytesCharged=j.used,budgetBytes=1000,priorSourceBytes=10,appSourceSha='b'*40,
            roster=self.roster,index=self.spec['index'],failureType=failure,checkpoint=state,
            budgetLedger=dict(namespace=self.sha,scope='global-lineage',lineageSha256='e'*64,checkpointSequence=j.seq,
                              unsettledReservedBytes=sum(j.pending.values())), outputs={},candidateCount=None,filesCompleted=None)
        ref=self.store.save_json(j.prefix+'results/',value)
        return value,ref
    def proof(self,*args,**kwargs):self.proofs.append((args,kwargs))
    def test_bootstrap_old_runner_same_spec_ledger_and_budget(self):
        value,ref=self.result()
        result=before_stage(self.store,self.spec,self.sha,self.current,ref,self.proof)
        self.assertEqual(result['chargedBytes'],11)
        self.assertTrue(self.proofs[0][1]['bootstrap'])
        with self.assertRaisesRegex(ValueError,'EXISTING_NAMESPACE'):
            before_stage(self.store,self.spec,self.sha,self.current,None,self.proof)
    def test_only_deadline_and_progress_and_budget_allow_continuation(self):
        value,ref=self.result()
        state=checkpoint(self.store,value,self.sha,self.spec)
        self.assertEqual(continuation(value,state,1),'resume')
        self.assertEqual(continuation(value,state,6),'exhausted')
        for reason in (None,'OSError','TimeoutError','ValueError','ResponseStreamingError'):
            with self.assertRaisesRegex(ValueError,'ONLY_EXPLICIT_DEADLINE'):
                continuation(dict(value,failureType=reason),state,1)
        no_progress=copy.deepcopy(value)
        no_progress['execution']['stageStartProgress']=dict(files=0,ranges=1,chargedBytes=11)
        with self.assertRaisesRegex(ValueError,'NO_DURABLE_PROGRESS'):continuation(no_progress,state,1)
        with self.assertRaisesRegex(ValueError,'BUDGET_EXHAUSTED'):
            continuation(dict(value,budgetBytes=11),state,1)
    def test_changed_spec_checkpoint_ledger_and_missing_receipt_stop(self):
        value,ref=self.result()
        with self.assertRaises(ValueError):load_result(self.store,ref,dict(self.spec,sourceBudgetBytes=999),self.sha)
        with self.assertRaises(ValueError):load_result(self.store,{},self.spec,self.sha)
        with self.assertRaises(ValueError):
            checkpoint(self.store,dict(value,sourceBytesCharged=12),self.sha,self.spec)
        j=self.journal();token=j.reserve(3);j.settle(token,1)
        with self.assertRaisesRegex(ValueError,'LEDGER_ADVANCED'):
            before_stage(self.store,self.spec,self.sha,self.current,ref,self.proof)
    def test_wrong_stage_or_runner_cannot_skip_predecessor(self):
        value,ref=self.result(context=self.current)
        for changes in (dict(stageIndex=3,jobKey='stage3'),dict(stageIndex=2,jobKey='stage2',runnerSourceSha='f'*40)):
            with self.assertRaisesRegex(ValueError,'IMMEDIATE_PREVIOUS'):
                before_stage(self.store,self.spec,self.sha,dict(self.current,**changes),ref,self.proof)
    def test_real_stage_deadline_records_start_and_previous_identity(self):
        old,prior=self.result()
        def run(store,spec,sha,work,context):
            value,ref=self.result(context=context)
            return {'privateResult':ref}
        marker=run_stage(self.store,self.spec,self.sha,self.root,self.current,prior,run,self.proof)
        self.assertEqual(marker['state'],'resume')
        value=load_result(self.store,marker['result'],self.spec,self.sha)
        self.assertEqual(value['execution']['stageStartProgress']['chargedBytes'],11)
        self.assertEqual(value['execution']['previousResult'],prior)
    def test_stage_output_from_another_job_cannot_be_reused(self):
        old,prior=self.result()
        def run(*args):
            value,ref=self.result(context=dict(self.current,jobKey='stage2'))
            return {'privateResult':ref}
        with self.assertRaisesRegex(ValueError,'CURRENT_STAGE_EXECUTION'):
            run_stage(self.store,self.spec,self.sha,self.root,self.current,prior,run,self.proof)
    def completed(self):
        value,ref=self.result(context=dict(self.current,previousResult=None),complete=True,failure=None)
        state=self.store.json(value['checkpoint']['key'],value['checkpoint']['sha256'],value['checkpoint']['bytes'])
        state['files']={str(i):{} for i in range(80)}
        value['checkpoint']=self.store.save_json(f'archive/collector/{self.sha}/snapshots/{state["seq"]:012d}-',state)
        self.store.client.objects={k:v for k,v in self.store.client.objects.items() if '/snapshots/' not in k or k==value['checkpoint']['key']}
        value['filesCompleted']=80;value['candidateCount']=9
        value['outputs']['summary.json']=self.blob(dict(filesCompleted=80,filesTotal=80,citiesRequested=6222,
            cityComplete={str(i):True for i in range(6222)},failures=[],geometryLookupFailures=[],uniqueNamedCandidates=9))
        ref=self.store.save_json(f'archive/collector/{self.sha}/results/',value)
        return value,ref
    def test_final_complete_requires_exact_scope_set_and_all_phase_checks(self):
        value,ref=self.completed()
        fake=types.SimpleNamespace(city_source_rows=lambda v: v['cities'], city_regions=lambda _: {str(i):None for i in range(6222)})
        with patch.dict(sys.modules,{'city_scope':fake}):
            complete_result(self.store,value,self.spec)
            for change in (dict(candidateCount=8),dict(filesCompleted=79),dict(complete=False),dict(failureType='Deadline')):
                with self.assertRaises(ValueError):complete_result(self.store,dict(value,**change),self.spec)
            fake.city_regions=lambda _: {str(i+1):None for i in range(6222)}
            with self.assertRaisesRegex(ValueError,'SCOPE_IDENTITIES'):complete_result(self.store,value,self.spec)
    def test_finalizer_accepts_only_complete_then_skipped_and_rejects_cancel(self):
        value,ref=self.completed()
        needs={f'stage{i}':dict(result='skipped',outputs={}) for i in range(1,7)}
        needs['stage1']=dict(result='success',outputs=dict(state='complete',receipt=json.dumps(ref)))
        fake=types.SimpleNamespace(city_source_rows=lambda v: v['cities'], city_regions=lambda _: {str(i):None for i in range(6222)})
        with patch.dict(sys.modules,{'city_scope':fake}):
            self.assertEqual(finalize(self.store,self.spec,self.sha,needs,self.current,self.proof),ref)
            for status in ('cancelled','failure'):
                bad=copy.deepcopy(needs);bad['stage1']['result']=status
                with self.assertRaisesRegex(ValueError,'FAILED_OR_CANCELLED'):
                    finalize(self.store,self.spec,self.sha,bad,self.current,self.proof)
            bad=copy.deepcopy(needs);bad['stage2']['result']='success'
            with self.assertRaisesRegex(ValueError,'WORK_AFTER_COMPLETE'):
                finalize(self.store,self.spec,self.sha,bad,self.current,self.proof)
    def test_restoration_consumes_total_deadline_instead_of_resetting_300min(self):
        with patch.dict(os.environ,{'COLLECTOR_STOP_AT':str(330*60)}):
            self.assertEqual(remaining_seconds({'maxMinutes':300},now=0),300*60)
            self.assertEqual(remaining_seconds({'maxMinutes':300},now=60*60),270*60)
            with self.assertRaises(Deadline):remaining_seconds({'maxMinutes':300},now=331*60)
        with patch.dict(os.environ,{'COLLECTOR_STOP_AT':'nan'}):
            with self.assertRaisesRegex(ValueError,'INVALID_STAGE_DEADLINE'):remaining_seconds({'maxMinutes':300},now=0)
    def test_six_deadlines_are_bounded_and_never_report_complete(self):
        _, previous = self.result()
        needs = {}
        def runner(store, spec, sha, work, context):
            _, ref = self.result(context=context)
            return {'privateResult': ref}
        for index in range(1, 7):
            current = dict(self.current, stageIndex=index, jobKey=f'stage{index}')
            marker = run_stage(self.store, self.spec, self.sha, self.root, current, previous, runner, self.proof)
            needs[f'stage{index}'] = dict(result='success', outputs=dict(state=marker['state'], receipt=json.dumps(marker['result'])))
            previous = marker['result']
        self.assertEqual(needs['stage6']['outputs']['state'], 'exhausted')
        with self.assertRaisesRegex(ValueError, 'FINITE_STAGES_EXHAUSTED'):
            finalize(self.store, self.spec, self.sha, needs, self.current, self.proof)

    def test_seed_installation_is_not_counted_as_new_attempt_progress(self):
        seed = dict(sourceFilesTotal=80, completedSourceFiles=[dict(name='seed', blob={})],
                    ranges=[dict(cacheKey='seed')])
        self.spec['contract'] = self.blob(dict(seed=self.blob(seed), namespaceLedgers=[], lineageSha256='e'*64))
        baseline = initial_progress(self.store, self.spec, self.sha)
        self.assertEqual((baseline['files'], baseline['ranges']), (1, 1))
        j = self.journal(); j.files['seed'] = {}; j.ranges['seed'] = {}; j.snapshot()
        value, ref = self.result(context=dict(self.current, stageStartProgress=baseline), progress=False)
        state = checkpoint(self.store, value, self.sha, self.spec)
        with self.assertRaisesRegex(ValueError, 'NO_DURABLE_PROGRESS'):
            continuation(value, state, 1)

    def test_stage_job_context_bound_and_finite(self):
        with patch('cloud_full_stage.execution',return_value=self.current),patch.dict(os.environ,{'GITHUB_JOB':'stage2'}):
            self.assertEqual(stage_context('c'*40,2)['stageIndex'],2)
            for stage in (0,7,True):
                with self.assertRaises(ValueError):stage_context('c'*40,stage)
            with self.assertRaisesRegex(ValueError,'JOB_KEY'):stage_context('c'*40,1)


if __name__=='__main__':unittest.main()
