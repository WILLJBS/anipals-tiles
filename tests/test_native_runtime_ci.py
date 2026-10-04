"""Synthetic runner outputs and failures; Docker and private storage are mocked."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'tools'),str(ROOT/'deploy')]
from native_runtime_ci import execute,docker_command,checked_outputs,OutputBudget
from native_runtime_fixture import request,blob,outputs,Store
from runtime_index_assembler import canonical_bytes


class RuntimeCITests(unittest.TestCase):
    def run_gate(self,fail=False,alter=None):
        spec=request();ref=blob(canonical_bytes(spec));context=dict(runnerSourceSha='a'*40)
        commands=[];store=Store()
        with tempfile.TemporaryDirectory() as temp:
            work=Path(temp)
            def runner(argv,log,timeout):
                commands.append((argv,timeout))
                with log.open('ab') as stream:stream.write(b'private synthetic evidence\n')
                if argv[:2]==['docker','run']:
                    if fail:raise subprocess.TimeoutExpired(argv,timeout)
                    folder=work/'runtime/output';folder.mkdir()
                    values=outputs(spec,ref,context)
                    if alter:alter(values)
                    for name,value in values.items():(folder/(name+'.json')).write_bytes(canonical_bytes(value))
            result=execute(store,spec,ref,context,work,runner)
        return result,store,commands
    def test_success_is_private_and_never_publishes_runtime_index(self):
        result,store,commands=self.run_gate()
        self.assertTrue(result['complete']);self.assertFalse(result['productionActivated'])
        self.assertFalse(result['published']);self.assertNotIn('private synthetic evidence',json.dumps(result))
        self.assertEqual(len(store.saved),8)
        for key,_ in store.saved:
            self.assertTrue(key.startswith('navigation/isolated-runtime-acceptance/'))
            self.assertNotIn('navigation/releases/',key)
        self.assertEqual(commands[-1][0][:3],['docker','rm','-f'])
        self.assertEqual([timeout for _,timeout in commands],[600,300,2100,30])
    def test_timeout_and_bad_native_identity_do_not_claim_complete(self):
        result,store,commands=self.run_gate(True)
        self.assertFalse(result['complete']);self.assertEqual(store.saved[-1][1]['error'],'TimeoutExpired')
        self.assertEqual(commands[-1][0][:3],['docker','rm','-f'])
        result,store,_=self.run_gate(alter=lambda values:values['session'].update(restart_verified=False))
        self.assertFalse(result['complete']);self.assertEqual(store.saved[-1][1]['privateArtifacts'],{})
    def test_source_inventory_and_candidate_chains_cannot_be_substituted(self):
        variants=[('preparation','request_sha256','0'*64),('session','production_activated',True),
                  ('preparation','source_bytes_reserved',2**70),('preparation','source_bytes_reserved',True),
                  ('session','probe_sha256','0'*64),('session','local_fingerprint','0'*64),
                  ('assembly-request','target_inventory',dict(sha256='0'*64))]
        for name,key,value in variants:
            with self.subTest(name=name,key=key):
                result,_,_=self.run_gate(alter=lambda values:values[name].update({key:value}))
                self.assertFalse(result['complete'])
    def test_coverage_identity_and_completion_expansion_are_rejected(self):
        changes=[lambda v:v['coverage'].update(tampered=True),
                 lambda v:v['inventory'].update(target_identity_sha256='e'*64),
                 lambda v:v['index']['review'].update(target_identity_sha256='e'*64),
                 lambda v:v['assembly-request'].update(target_identity_sha256='e'*64),
                 lambda v:v['session'].update(scope_routes_verified=True),
                 lambda v:v['index']['review'].update(runtime_activated=True),
                 lambda v:v['index']['review'].update(production_supply_complete=True)]
        for index,change in enumerate(changes):
            with self.subTest(index=index):
                def altered(values):
                    change(values)
                    inv=blob(canonical_bytes(values['inventory']))['sha256']
                    values['preparation']['inventory_sha256']=inv
                    values['assembly-request']['target_inventory']['sha256']=inv
                    values['index']['review']['target_inventory_sha256']=inv
                    assembly=blob(canonical_bytes(values['assembly-request']))['sha256']
                    values['session']['request_sha256']=assembly
                    values['index']['review']['request_sha256']=assembly
                    digest=blob(canonical_bytes(values['index']))['sha256']
                    values['session']['index_sha256']=digest;values['preparation']['index_sha256']=digest
                result,_,_=self.run_gate(alter=altered)
                self.assertFalse(result['complete'])

    def test_invalid_or_noncanonical_input_is_rejected_before_build(self):
        value=request();store=Store()
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError,'CANONICAL'):
                execute(store,value,blob(b'noncanonical'),dict(runnerSourceSha='a'*40),Path(temp),
                        lambda *_:self.fail('Docker was called'))
        self.assertEqual(store.saved,[])
    def test_container_mounts_are_fixed_owned_paths_and_credentials_are_not_argv(self):
        value=request();ref=blob(canonical_bytes(value))
        with patch.dict(os.environ,{'R2_SECRET_ACCESS_KEY':'SYNTHETIC_SECRET'}):
            argv=docker_command(value,ref,Path('/tmp/owned-ci'),'owned-container')
        text=' '.join(argv)
        self.assertNotIn('SYNTHETIC_SECRET',text);self.assertNotIn('--privileged',argv)
        self.assertIn('RUNNER_TEMP=/isolated',argv);self.assertIn('1536m',argv)
        self.assertIn('type=bind,src=/tmp/owned-ci/runtime,dst=/isolated',argv)
        self.assertIn('GITHUB_WORKFLOW_SHA',argv);self.assertIn('GITHUB_RUN_ID',argv)
        self.assertNotIn('/data',text)
    def test_output_budget_is_charged_before_upload(self):
        store=Store();budget=OutputBudget(store,10)
        with self.assertRaisesRegex(ValueError,'BUDGET'):budget.save('private/',dict(large='x'*20))
        self.assertEqual(store.saved,[])
    def test_symlink_extra_and_large_output_rejected(self):
        spec=request();ref=blob(canonical_bytes(spec));context=dict(runnerSourceSha='a'*40)
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp)
            for name,value in outputs(spec,ref,context).items():(folder/(name+'.json')).write_bytes(canonical_bytes(value))
            (folder/'extra').touch()
            with self.assertRaisesRegex(ValueError,'OUTPUT_SET'):checked_outputs(folder,spec,ref,context)
            (folder/'extra').unlink();(folder/'coverage.json').unlink()
            (folder/'coverage.json').symlink_to(folder/'inventory.json')
            with self.assertRaisesRegex(ValueError,'OUTPUT_BOUNDS'):checked_outputs(folder,spec,ref,context)
    def test_workflow_default_and_independent_runtime_group_are_explicit(self):
        text=(ROOT/'.github/workflows/native-r2-acceptance.yml').read_text()
        self.assertIn('default: read-only',text)
        self.assertIn("!inputs.acceptance_mode || inputs.acceptance_mode == 'read-only'",text)
        self.assertIn("inputs.acceptance_mode == 'runtime-isolated' && 'native-runtime-isolated' || 'native-r2-acceptance'",text)
        self.assertIn('python tools/native_runtime_ci.py --source-sha "$SOURCE_SHA"',text)
        self.assertIn('timeout-minutes: 60',text);self.assertIn('timeout-minutes: 25',text)
        self.assertNotIn('upload-artifact',text);self.assertNotIn('push:',text)


if __name__=='__main__':unittest.main()
