"""All storage jobs/full test suites use the reviewed SDK model, never Ubuntu's."""
from pathlib import Path
import re
import importlib.metadata
import unittest
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
from ci_workflow_policy import job_steps

REQUIREMENTS = 'tools/storage-requirements.txt'
STORAGE_WORKFLOWS = {'migrate-release-r2.yml', 'display-basemap-publish.yml',
                     'verify-private-archive.yml', 'collect-private-places.yml',
                     'collect-private-places-full.yml', 'native-r2-acceptance.yml'}


def requirement_errors(text):
    pins = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        match = re.fullmatch(r'(boto3|botocore)==(\d+\.\d+\.\d+)', line)
        if not match or match[1] in pins:
            return ['SDK requirements must be exact unique pins']
        pins[match[1]] = match[2]
    if set(pins) != {'boto3', 'botocore'} or len(set(pins.values())) != 1:
        return ['boto3 and botocore must share one reviewed version']
    return []


def storage_errors(text, storage_workflow=False, root=ROOT):
    errors = []
    try:
        jobs = job_steps(text, root)
    except ValueError as error:
        return [('workflow', str(error))]
    for name, steps in jobs:
        python_ready = sdk_ready = False
        for step in steps:
            if not re.match(r'\n?      - ', step):
                continue
            conditional = bool(re.search(r'^(?:        |      - )(?:if:|continue-on-error:)', step, re.M))
            if 'uses: actions/setup-python@' in step:
                if conditional:
                    errors.append((name, 'Python preparation cannot be conditional or ignore errors'))
                python_ready = bool(re.search(r'''python-version:\s*['"]?3\.12['"]?\s*$''', step, re.M))
                sdk_ready = False
            lines = [line for line in step.splitlines() if not line.lstrip().startswith('#')]
            commands = '\n'.join(lines).replace('\\\n', ' ')
            if re.search(r'\bpip\s+install\b', commands):
                if re.search(r'''(?:\s|['"])(?:boto3|botocore)(?=[=<>!\s'"]|$)''', commands):
                    errors.append((name, 'SDK pins must only come from requirements'))
                if re.search(r'\b(?:python|python3)\s+-m\s+pip\s+install\b[^\n]*\s-r\s+' + re.escape(REQUIREMENTS) + r'(?:\s|$)', commands):
                    if conditional or re.search(r'\|\||;\s*true\b', commands):
                        errors.append((name, 'SDK preparation cannot be conditional or ignore errors'))
                    if not python_ready:
                        errors.append((name, 'SDK install must follow Python 3.12 setup'))
                    sdk_ready = python_ready
            for command in commands.splitlines():
                unittest = bool(re.search(r'\bpython3?\s+-m\s+unittest\s+discover\b', command))
                full_suite = unittest and not re.search(r'(?:\s-p\s|--pattern(?:\s|=))', command)
                storage_command = storage_workflow and (unittest or bool(re.search(r'\bpython3?\s+(?:-u\s+)?tools/', command)))
                if (full_suite or storage_command) and not sdk_ready:
                    errors.append((name, 'tests/storage command precedes pinned SDK preparation'))
        if storage_workflow and not sdk_ready:
            errors.append((name, 'storage job has no pinned SDK preparation'))
    return errors


SETUP = """      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
"""
INSTALL = '      - run: python -m pip install --disable-pip-version-check -r tools/storage-requirements.txt\n'
TEST = '      - run: PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v\n'


def workflow(steps):
    return 'name: fixture\njobs:\n  test:\n    steps:\n' + steps


class CiStoragePolicy(unittest.TestCase):
    def test_single_requirements_pin_sdk_and_service_model_together(self):
        self.assertEqual(requirement_errors((ROOT / REQUIREMENTS).read_text()), [])
        for bad in ['boto3>=1.42\nbotocore==1.42.97', 'boto3==1.42.97',
                    'boto3==1.42.97\nbotocore==1.42.96', 'boto3==1.42.97\nboto3==1.42.97\nbotocore==1.42.97']:
            self.assertTrue(requirement_errors(bad))

    def test_every_full_suite_and_storage_job_prepares_sdk_first(self):
        paths = list((ROOT / '.github/workflows').glob('*.yml'))
        self.assertTrue(STORAGE_WORKFLOWS <= {p.name for p in paths})
        for path in paths:
            with self.subTest(workflow=path.name):
                self.assertEqual(storage_errors(path.read_text(), path.name in STORAGE_WORKFLOWS), [])

    def test_installed_sdk_matches_pins_and_supports_conditional_writes(self):
        from botocore.session import Session
        for line in (ROOT / REQUIREMENTS).read_text().splitlines():
            if line and not line.startswith('#'):
                name, version = line.split('==')
                self.assertEqual(importlib.metadata.version(name), version)
        model = Session().get_service_model('s3')
        for operation in ('PutObject', 'CompleteMultipartUpload'):
            self.assertIn('IfNoneMatch', model.operation_model(operation).input_shape.members)

    def test_negative_controls_reject_original_system_sdk_and_ordering_drift(self):
        self.assertEqual(storage_errors(workflow(SETUP + INSTALL + TEST)), [])
        for steps in (TEST, INSTALL + TEST, SETUP + TEST + INSTALL,
                      SETUP.replace('3.12', '3.11') + INSTALL + TEST,
                      SETUP + INSTALL + SETUP + TEST,
                      SETUP.replace('        with:', '        if: false\n        with:') + INSTALL + TEST,
                      SETUP + INSTALL.replace('      - run:', '      - if: false\n        run:') + TEST,
                      SETUP + INSTALL.replace('requirements.txt', 'requirements.txt || true') + TEST,
                      SETUP + '      - run: pip install boto3==1.42.97\n' + TEST,
                      SETUP + INSTALL + '      - run: python -m pip install botocore==1.0.0\n' + TEST):
            with self.subTest(steps=steps):
                self.assertTrue(storage_errors(workflow(steps)))
        partial = "      - run: python -m unittest discover -s tests -p 'test_private_archive.py'\n"
        self.assertTrue(storage_errors(workflow(SETUP + partial + INSTALL), True))
        self.assertTrue(storage_errors(workflow(SETUP + '      - run: python tools/private_archive_verify.py\n' + INSTALL), True))
        self.assertTrue(storage_errors(workflow(SETUP + INSTALL) + '  second:\n    steps:\n' + TEST))


if __name__ == '__main__':
    unittest.main()
