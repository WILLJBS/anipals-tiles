import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
from check_global_build import validate_inputs
from regional_release import validate_native_probes


class GlobalBuildInputsTests(unittest.TestCase):
    def test_committed_cloud_inputs_cover_every_frozen_source_row_without_duplicate_builds(self):
        result = validate_inputs(ROOT/'deploy')
        self.assertEqual(result, dict(source_rows=6222, local_rows=4980, addition_rows=1120,
            gap_rows=122, additions=129, gap_graphs=3, coalesced_children=9))
        scopes = json.loads((ROOT/'deploy/global-additions-scopes.json').read_text())
        self.assertEqual(sum(r['source_rows'] for r in scopes['coalesced']), 103)
        skipped = {r['source_id'] for r in scopes['coalesced']}
        self.assertEqual(skipped, {'bahamas','belize','cuba','el-salvador','guatemala',
                                  'haiti-and-domrep','honduras','jamaica','nicaragua'})
        roster = json.loads((ROOT/'deploy/global-additions.json').read_text())
        self.assertFalse({r['region'].split('/')[-1] for r in roster['region']} & skipped)

    def test_checker_runs_as_a_standalone_ci_command_without_ignored_inputs(self):
        output = subprocess.check_output([sys.executable, str(ROOT/'tools/check_global_build.py'), '--root', str(ROOT/'deploy')], text=True)
        self.assertEqual(json.loads(output)['source_rows'], 6222)

    def test_tampered_source_mapping_and_removed_native_scope_fail_closed(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            for path in (ROOT/'deploy').glob('global-*.json'): shutil.copyfile(path, root/path.name)
            shutil.copyfile(ROOT/'deploy/coverage.json', root/'coverage.json')
            file = root/'global-additions-scopes.json'
            mapping = json.loads(file.read_text()); mapping['builds'][0]['probes'].pop()
            file.write_text(json.dumps(mapping))
            with self.assertRaisesRegex(ValueError, 'byte SHA256'):
                validate_inputs(root)
            roster = json.loads((root/'global-additions.json').read_text())
            roster['scope_file_sha256'] = hashlib.sha256(file.read_bytes()).hexdigest()
            (root/'global-additions.json').write_text(json.dumps(roster))
            with self.assertRaisesRegex(ValueError, 'native probes omit'):
                validate_inputs(root)

    def test_native_ready_proof_must_name_every_exact_source_row(self):
        feature = json.loads((ROOT/'deploy/global-gap-coverage.json').read_text())['features'][0]
        props = feature['properties']
        proof = dict(slug=props['slug'], native_version='3.3.0', scope_sha256=props['native_probe_sha256'],
            probes=[dict(source_row=row, verified=True, distance_km=.2) for row in props['native_source_rows']])
        validate_native_probes({'native_validation':proof}, feature, props['slug'])
        for fault in ('missing','different-city','wrong-hash','zero-distance'):
            broken = copy.deepcopy(proof)
            if fault == 'missing': broken['probes'].pop()
            if fault == 'different-city': broken['probes'][0]['source_row'] = 999999
            if fault == 'wrong-hash': broken['scope_sha256'] = '0'*64
            if fault == 'zero-distance': broken['probes'][0]['distance_km'] = 0
            with self.subTest(fault=fault), self.assertRaises(ValueError):
                validate_native_probes({'native_validation':broken}, feature, props['slug'])


if __name__ == '__main__':
    unittest.main()
