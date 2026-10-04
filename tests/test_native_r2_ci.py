"""Private request/key/argv boundaries; fake runner never reaches Docker or R2."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
from native_r2_contract import SCHEMA, WORKFLOW, descriptor, execution, validate_request
from native_r2_ci import docker_command, execute


def blob(sha, size=100):
    return dict(key=f'archive/sha256/{sha[:2]}/{sha}', sha256=sha, bytes=size)


def fixture():
    source = 'a'*40
    value = dict(schema=SCHEMA, diagnosticSha=source, migrationSha='b'*40,
                 contract='original61', tag='tiles-test', slug='canada', graphFingerprint='c'*64,
                 release=blob('d'*64), ready=blob('e'*64), probe=blob('f'*64))
    value['receipt'] = dict(key='navigation/migration-receipts/%s/tiles-test/receipt-canada/%s.json' % ('b'*40, '1'*64),
                            sha256='1'*64, bytes=100)
    value['manifest'] = dict(key='navigation/graphs/canada/%s/manifests/%s.json' % ('c'*64, '2'*64),
                             sha256='2'*64, bytes=100)
    return value


class ContractTests(unittest.TestCase):
    def test_exact_request_and_descriptors(self):
        value = fixture()
        self.assertEqual(validate_request(value, 'a'*40), value)
        for key, replacement in [('schema', 'other'), ('diagnosticSha', '0'*40), ('migrationSha', 'HEAD'),
                                 ('slug', '../other'), ('tag', '--help'), ('contract', 'partial127'),
                                 ('graphFingerprint', 'x'*64)]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_request(dict(value, **{key: replacement}), 'a'*40)
        with self.assertRaises(ValueError):
            validate_request(dict(value, command='echo secret'), 'a'*40)

    def test_cross_namespace_urls_unknown_fields_bool_and_nan_refused(self):
        for field in ('release', 'ready', 'probe', 'receipt', 'manifest'):
            for key, wrong in [('key', 'https://untrusted.invalid/key'), ('key', '../../secret'),
                               ('bytes', True), ('bytes', 0), ('bytes', float('nan')), ('bytes', 40*1024*1024),
                               ('sha256', 'x'*64), ('extra', 'command')]:
                value = fixture(); value[field][key] = wrong
                with self.subTest(field=field, key=key), self.assertRaises(ValueError):
                    validate_request(value, 'a'*40)
        with self.assertRaises(ValueError):
            descriptor(dict(key='archive/sha256/aa/'+'a'*64, sha256='b'*64, bytes=1), 65536)

    def test_exact_workflow_and_runner_context(self):
        source, ref = 'a'*40, 'refs/heads/staging'
        env = dict(GITHUB_ACTIONS='true', GITHUB_REPOSITORY='WILLJBS/anipals-tiles', GITHUB_SHA=source,
                   GITHUB_WORKFLOW_SHA=source, GITHUB_REF=ref, GITHUB_RUN_ID='123', GITHUB_RUN_ATTEMPT='1',
                   GITHUB_WORKFLOW_REF=f'WILLJBS/anipals-tiles/{WORKFLOW}@{ref}')
        self.assertEqual(execution(source, env)['runId'], 123)
        for key, bad in [('GITHUB_WORKFLOW_SHA', 'b'*40), ('GITHUB_REPOSITORY', 'other/repo'),
                         ('GITHUB_WORKFLOW_REF', 'other'), ('GITHUB_RUN_ID', '0')]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                execution(source, dict(env, **{key: bad}))

    def test_workflow_only_five_dispatch_inputs_and_no_public_artifacts(self):
        import re
        text = (ROOT/'.github/workflows/native-r2-acceptance.yml').read_text()
        inputs = text.split('    inputs:\n', 1)[1].split('permissions:', 1)[0]
        self.assertEqual(set(re.findall(r'^      ([a-z_]+):$', inputs, re.M)),
                         {'source_sha', 'bucket', 'input_key', 'input_sha', 'input_bytes'})
        self.assertNotIn('upload-artifact', text)
        self.assertNotIn('push:', text)
        self.assertIn('persist-credentials: false', text)
        self.assertIn('timeout-minutes: 25', text)
        self.assertIn('-r tools/storage-requirements.txt', text)

    def test_docker_args_never_contain_credentials_or_requested_command(self):
        value = fixture()
        with patch.dict(os.environ, R2_SECRET_ACCESS_KEY='PRIVATE_SENTINEL'):
            argv = docker_command(value, Path('/tmp/work'), 'diagnostic-owned')
        self.assertIsInstance(argv, list)
        self.assertNotIn('PRIVATE_SENTINEL', ' '.join(argv))
        self.assertNotIn('--privileged', argv)
        self.assertEqual(argv[0:2], ['docker', 'run'])
        self.assertNotIn('--rm', argv)  # Explicit finally cleanup, including timeout.
        self.assertIn('--manifest-size', argv)
        self.assertIn('--graph-fingerprint', argv)


class FakeStore:
    def __init__(self): self.saved = []; self.fetched = []
    def fetch(self, desc, path): self.fetched.append(desc); path.write_text('{}')
    def save_json(self, prefix, value):
        self.saved.append((prefix, value))
        return dict(key=prefix+'9'*64+'.json', sha256='9'*64, bytes=100)


class RuntimeTests(unittest.TestCase):
    def run_gate(self, fail=False):
        value, store, commands = fixture(), FakeStore(), []
        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp)/'work'
            def runner(argv, log, timeout):
                commands.append((argv, timeout))
                with log.open('ab') as out: out.write(b'private route evidence\n')
                if argv[:2] == ['docker', 'run']:
                    if fail: raise subprocess.TimeoutExpired(argv, timeout)
                    result = dict(event='REAL_R2_NATIVE_COLD_HOT_PASSED', diagnostic_sha='a'*40, migration_sha='b'*40,
                                  receipt_sha256='1'*64, manifest_sha256='2'*64, probe_sha256='f'*64,
                                  graph_fingerprint='c'*64, memory_mb=768, timeout_seconds=8, production_activated=False,
                                  records=[dict(cache='cold', http_gets=3, object_fetches=3, source_bytes=120,
                                                elapsed_ms=200, distance_km=.1),
                                           dict(cache='hot', http_gets=0, object_fetches=0, source_bytes=0,
                                                elapsed_ms=120, distance_km=.1)])
                    (work/'output/result.json').write_text(json.dumps(result))
            result = execute(store, value, blob('3'*64), dict(runnerSourceSha='a'*40), work, runner)
        return result, store, commands

    def test_success_only_opaque_descriptors_and_counters_public(self):
        result, store, commands = self.run_gate()
        self.assertTrue(result['complete'])
        self.assertEqual(set(result), {'complete', 'privateResult', 'productionActivated', 'counters'})
        self.assertNotIn('private route evidence', json.dumps(result))
        self.assertNotIn('distance_km', json.dumps(result))
        self.assertEqual(len(store.fetched), 3)
        self.assertEqual(len(store.saved), 2)
        self.assertEqual(commands[-1][0][:3], ['docker', 'rm', '-f'])
        self.assertEqual([timeout for _, timeout in commands], [600, 300, 180, 30])
        self.assertTrue(store.saved[-1][1]['complete'])

    def test_timeout_always_stops_owned_container_saves_private_failure(self):
        result, store, commands = self.run_gate(True)
        self.assertFalse(result['complete'])
        self.assertEqual(commands[-1][0][:3], ['docker', 'rm', '-f'])
        self.assertNotIn('native', result)
        self.assertEqual(store.saved[-1][1]['error'], 'TimeoutExpired')
        self.assertEqual(store.saved[-1][1]['phase'], 'native-cold-hot')

    def test_invalid_request_fails_before_any_private_read_or_docker(self):
        value, store = fixture(), FakeStore(); value['probe']['key'] = 'arbitrary'
        with tempfile.TemporaryDirectory() as temp, self.assertRaises(ValueError):
            execute(store, value, blob('3'*64), dict(runnerSourceSha='a'*40), Path(temp),
                    lambda *_: self.fail('Docker called'))
        self.assertEqual(store.fetched, [])
        self.assertEqual(store.saved, [])


if __name__ == '__main__':
    unittest.main()
