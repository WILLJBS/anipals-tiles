"""Reviewed local actions remain subject to the same install/SDK/timeout gates."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_ci_storage_policy import storage_errors, workflow, SETUP, INSTALL, TEST
from test_ci_install_policy import install_errors
from ci_workflow_policy import composite_path, reviewed_composites, timeout_errors, job_steps

USE = '      - uses: ./.github/actions/full-collector-stage\n'


class CompositePolicyTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)/'repo'; self.root.mkdir()
        self.action = self.root/'.github/actions/full-collector-stage/action.yml'
        self.action.parent.mkdir(parents=True)
    def action_steps(self, steps):
        self.action.write_text('name: reviewed\nruns:\n  using: composite\n  steps:\n'
                               + '\n'.join(line[2:] for line in steps.splitlines())+'\n')
    def test_reviewed_action_expands_and_enforces_exact_setup_order(self):
        self.action_steps(SETUP+INSTALL+TEST)
        self.assertEqual(storage_errors(workflow(USE),True,self.root),[])
        self.assertEqual(len(job_steps(workflow(USE),self.root)[0][1]),3)
        self.assertEqual(reviewed_composites(self.root),[self.action.resolve()])
        for steps in (TEST, SETUP+TEST+INSTALL, INSTALL+TEST,
                      SETUP+INSTALL.replace('-r tools/storage-requirements.txt','boto3>=1')+TEST,
                      SETUP.replace('3.12','3.11')+INSTALL+TEST,
                      SETUP+INSTALL.replace('requirements.txt','requirements.txt || true')+TEST):
            self.action_steps(steps)
            self.assertTrue(storage_errors(workflow(USE),True,self.root))
    def test_composite_preparation_cannot_be_conditional_or_ignore_failure(self):
        self.action_steps(SETUP+INSTALL+TEST)
        for key in ('if: false','continue-on-error: true'):
            self.assertTrue(storage_errors(workflow(USE+'        '+key+'\n'),True,self.root))
    def test_missing_unknown_and_escaping_composites_fail_closed(self):
        for use in (USE,USE.replace('full-collector-stage','unknown'),
                    USE.replace('./.github/actions/full-collector-stage','../outside'),
                    USE.replace('./.github/actions/full-collector-stage','./.github/actions/../../outside')):
            self.assertTrue(storage_errors(workflow(use),True,self.root))
        outside=Path(self.temp.name)/'outside.yml';outside.write_text('runs:\n  using: composite\n  steps: []\n')
        self.action.symlink_to(outside)
        with self.assertRaisesRegex(ValueError,'outside repository'):
            composite_path('./.github/actions/full-collector-stage',self.root)
    def test_non_composite_cyclic_and_unregistered_action_are_rejected(self):
        self.action.write_text('runs:\n  using: node20\n  main: index.js\n')
        self.assertTrue(storage_errors(workflow(USE),True,self.root))
        self.action_steps(USE)
        self.assertTrue(storage_errors(workflow(USE),True,self.root))
        self.action_steps(SETUP+INSTALL)
        unknown=self.root/'.github/actions/unknown/action.yaml';unknown.parent.mkdir();unknown.write_text('runs: {}')
        with self.assertRaisesRegex(ValueError,'registry differs'):reviewed_composites(self.root)
    def test_package_prompt_gate_also_applies_to_composite_commands(self):
        self.action_steps(SETUP+INSTALL+'      - run: apt-get install -y tzdata\n')
        self.assertTrue(install_errors(self.action.read_text()))
    def test_missing_or_excessive_stage_timeouts_are_rejected(self):
        template='name: fixture\njobs:\n  {job}:\n    timeout-minutes: {minutes}\n    steps: []\n'
        for job,minutes in [('full',31),('stage1',351),('stage6',351),('finalize',31),('stage7',350)]:
            self.assertTrue(timeout_errors(template.format(job=job,minutes=minutes),'collect-private-places-full.yml'))
        missing=template.format(job='stage1',minutes=350).replace('    timeout-minutes: 350\n','')
        self.assertTrue(timeout_errors(missing,'collect-private-places-full.yml'))
        for job,minutes in [('full',30),('stage1',350),('stage6',350),('finalize',30)]:
            self.assertEqual(timeout_errors(template.format(job=job,minutes=minutes),'collect-private-places-full.yml'),[])


if __name__=='__main__':unittest.main()
