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
sys.path.insert(0, str(ROOT/'deploy'))
from check_global_build import validate_inputs
from prepare_global_build import load_corrections
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

    def test_explicit_unavailable_scope_proof_is_accepted_only_fully_classified(self):
        feature = json.loads((ROOT/'deploy/global-gap-coverage.json').read_text())['features'][0]
        props = feature['properties']
        rows = props['native_source_rows']
        proof = dict(slug=props['slug'], native_version='3.3.0', scope_sha256=props['native_probe_sha256'],
            probes=[dict(source_row=rows[0], name='X', verified=False, classification='scope_unavailable')]
                   + [dict(source_row=row, verified=True, distance_km=.2) for row in rows[1:]])
        validate_native_probes({'native_validation':proof}, feature, props['slug'])
        for fault, mutation in (('unclassified', dict(verified=False, distance_km=.2)),
                                ('extra-field', dict(name='X', verified=False, classification='scope_unavailable', distance_km=.2)),
                                ('unknown-class', dict(name='X', verified=False, classification='wiggle')),
                                ('duplicate', None)):
            broken = copy.deepcopy(proof)
            if fault == 'duplicate': broken['probes'].append(copy.deepcopy(broken['probes'][0]))
            else: broken['probes'][0] = dict(source_row=rows[0], **mutation)
            with self.subTest(fault=fault), self.assertRaises(ValueError):
                validate_native_probes({'native_validation':broken}, feature, props['slug'])

    def test_committed_corrections_relocate_and_retire_exactly_the_two_failed_probes(self):
        corrections = json.loads((ROOT/'deploy/global-scope-corrections.json').read_text())
        self.assertEqual([c['source_row'] for c in corrections['corrections']], [3457, 6087])
        self.assertEqual({c['kind'] for c in corrections['corrections']},
                         {'verified_target_relocation', 'scope_unavailable'})
        scopes = json.loads((ROOT/'deploy/global-additions-scopes.json').read_text())
        probes = {p['source_row']: p for b in scopes['builds'] for p in b['probes']}
        relocated, retired = probes[3457], probes[6087]
        self.assertEqual(relocated['probe_correction'], 'verified_target_relocation')
        self.assertAlmostEqual(relocated['lat'], 10.42542476); self.assertAlmostEqual(relocated['lng'], -66.78627871)
        self.assertNotEqual((relocated['lat'], relocated['lng']),
                            (corrections['corrections'][0]['original']['lat'], corrections['corrections'][0]['original']['lng']))
        self.assertEqual(retired['probe_correction'], 'scope_unavailable')
        self.assertEqual((retired['lat'], retired['lng']),
                         (corrections['corrections'][1]['original']['lat'], corrections['corrections'][1]['original']['lng']))

    def test_correction_validation_fails_closed_on_drift(self):
        rows = [dict(source_row=0, name='A', country='X', lat=1.0, lng=2.0),
                dict(source_row=1, name='B', country='Y', lat=3.0, lng=4.0)]
        def corrections(**overrides):
            entry = dict(source_row=0, slug='graph-a', kind='verified_target_relocation',
                         original=dict(lat=1.0, lng=2.0), probe=dict(lat=1.5, lng=2.5),
                         basis=dict(kind='published-navigation-target'))
            entry.update(overrides)
            return json.dumps(dict(schema=1, comment='test', corrections=[entry])).encode()
        load_corrections(corrections(), rows)
        for fault, payload in (('stale-original', corrections(original=dict(lat=9.0, lng=2.0))),
                               ('unknown-row', corrections(source_row=7)),
                               ('unknown-kind', corrections(kind='wiggle')),
                               ('missing-basis', corrections(basis=None)),
                               ('out-of-range', corrections(probe=dict(lat=95.0, lng=2.5))),
                               ('duplicate', json.dumps(dict(schema=1, comment='t', corrections=[
                                   json.loads(corrections()), json.loads(corrections())])).encode())):
            with self.subTest(fault=fault), self.assertRaises(ValueError):
                load_corrections(payload, rows)


if __name__ == '__main__':
    unittest.main()
