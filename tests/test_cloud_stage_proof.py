import copy
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from cloud_stage_proof import github_stage_proof
from cloud_collect_full import WORKFLOW
from cloud_execution import REPOSITORY


class GitHubStageProofTest(unittest.TestCase):
    def setUp(self):
        self.ref=dict(key='private/result',sha256='a'*64,bytes=99)
        self.execution=dict(kind='github-actions',repository=REPOSITORY,workflowPath=WORKFLOW,
            runId=42,runAttempt=1,runnerSourceSha='b'*40,workflowSha='b'*40,stageIndex=1,jobKey='stage1')
        self.result=dict(execution=self.execution)
        self.current=dict(runId=42,runAttempt=1)
        self.run=dict(id=42,run_attempt=1,head_sha='b'*40,path=WORKFLOW,event='workflow_dispatch',
                      repository=dict(full_name=REPOSITORY),status='in_progress',conclusion=None)
        self.job=dict(id=99,run_id=42,head_sha='b'*40,name='collector-stage-1',status='completed',conclusion='success')
        self.log=json.dumps({'collectorStage':dict(result=self.ref,stageIndex=1,jobKey='stage1',state='resume')}).encode()
        self.redirect=None;self.calls=[]
    def fetch(self,url,token=None,**kwargs):
        self.calls.append((url,token,kwargs))
        if '/logs' in url: return self.redirect or self.log
        if '.blob.core.windows.net' in url: return self.log
        if '/jobs?' in url:return json.dumps(dict(total_count=1,jobs=[self.job])).encode()
        return json.dumps(self.run).encode()
    def proof(self,**kw):
        return github_stage_proof(self.result,self.ref,self.current,fetch=self.fetch,**kw)
    def test_completed_actual_job_exact_log_is_required(self):
        self.assertEqual(self.proof()['jobId'],99)
        self.log=b'{"collectorStage":{"result":{}}}'
        with self.assertRaisesRegex(ValueError,'NOT_IN_ACTUAL_JOB_LOG'):self.proof()
    def test_stage_identity_actual_sha_cancellation_and_attempt_are_bound(self):
        for key,value in [('head_sha','c'*40),('run_attempt',2),('conclusion','cancelled')]:
            old=self.run[key];self.run[key]=value
            with self.assertRaises(ValueError):self.proof()
            self.run[key]=old
        for key,value in [('conclusion','failure'),('status','in_progress'),('head_sha','d'*40),('name','collector-stage-2')]:
            old=self.job[key];self.job[key]=value
            with self.assertRaises(ValueError):self.proof()
            self.job[key]=old
        self.result['execution']=dict(self.execution,jobKey='stage2')
        with self.assertRaisesRegex(ValueError,'JOB_IDENTITY'):self.proof()
    def test_old_legacy_full_failure_requires_terminal_run_and_exact_receipt(self):
        self.result['execution']={k:v for k,v in self.execution.items() if k not in ('stageIndex','jobKey')}
        self.current=dict(runId=43,runAttempt=1);self.job.update(name='full',conclusion='failure')
        self.log=json.dumps(dict(complete=False,privateResult=self.ref)).encode()
        with self.assertRaisesRegex(ValueError,'TERMINAL_FAILED_RUN'):self.proof(bootstrap=True)
        self.run.update(status='completed',conclusion='failure')
        self.assertEqual(self.proof(bootstrap=True)['runId'],42)
        self.run['conclusion']='cancelled'
        with self.assertRaisesRegex(ValueError,'TERMINAL_FAILED_RUN'):self.proof(bootstrap=True)
    def test_token_never_follows_signed_log_redirect(self):
        self.redirect='https://example.blob.core.windows.net/log?sig=private'
        with patch.dict(os.environ,{'GH_TOKEN':'offline-secret'}):
            self.proof()
        self.assertEqual(self.calls[-1][1],None)
        self.assertTrue(all(token=='offline-secret' for _,token,_ in self.calls[:-1]))
        self.redirect='https://attacker.invalid/log'
        with self.assertRaisesRegex(ValueError,'REDIRECT_REJECTED'):self.proof()
    def test_wrong_run_context_or_duplicate_job_is_rejected(self):
        self.current['runAttempt']=2
        with self.assertRaisesRegex(ValueError,'RUN_ATTEMPT'):self.proof()
        self.current['runAttempt']=1
        base=self.fetch
        def duplicate(url,token=None,**kw):
            if '/jobs?' in url:return json.dumps(dict(total_count=2,jobs=[self.job,self.job])).encode()
            return base(url,token,**kw)
        with self.assertRaisesRegex(ValueError,'UNIQUE_ACTUAL_STAGE_JOB'):
            github_stage_proof(self.result,self.ref,self.current,fetch=duplicate)


if __name__=='__main__':unittest.main()
