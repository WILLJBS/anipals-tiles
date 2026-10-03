import copy
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
from migration_contract import load_profile, validate_profile_supply, source_contract, migration_identity
import migration_ci
import migrate_release_r2 as cli


def fixture():
    data = json.loads((ROOT/'tests/fixtures/gap-release-20261003.json').read_text())
    return data['release'], data['ready_utf8'].encode()


class MigrationContractTests(unittest.TestCase):
    def test_cloud_commands_bind_reviewed_source_and_registry(self):
        workflow = (ROOT/'.github/workflows/migrate-release-r2.yml').read_text()
        self.assertIn('options: [original61, gap3, additions129]', workflow)
        for line in workflow.splitlines():
            if 'python3 ' not in line: continue
            if 'tools/migration_ci.py' in line or 'tools/migrate_release_r2.py' in line:
                self.assertIn('--contract "$MIGRATION_CONTRACT"', line)
                self.assertIn('--source-sha "$SOURCE_SHA"', line)

    def test_registered_rosters_are_disjoint_and_exact_193_graphs(self):
        seen = set()
        for name, count in [('original61', 61), ('gap3', 3), ('additions129', 129)]:
            profile = load_profile(name); slugs = {r['slug'] for r in profile['roster']['region']}
            self.assertEqual(len(slugs), count); self.assertFalse(seen & slugs); seen.update(slugs)
        self.assertEqual(len(seen), 193)
        with self.assertRaises(ValueError): load_profile('arbitrary-local-path')

    def test_actual_published_three_graph_ready_passes_same_supply_model(self):
        release, ready = fixture(); plans = validate_profile_supply(load_profile('gap3'), release, ready)
        self.assertEqual({p['slug'] for p in plans}, {'central-america', 'port-aux-francais-window', 'grytviken-window'})
        payload = json.loads(ready)
        self.assertEqual(sum(len(m['native_validation']['probes']) for m in payload['region_manifests'].values()), 122)
        def fetch(url, token=None, **kwargs):
            if '/releases/tags/' in url: return json.dumps(release).encode()
            if '/assets?' in url: return json.dumps(release['assets']).encode()
            return ready
        with tempfile.TemporaryDirectory() as directory, patch.object(migration_ci, 'fetch', side_effect=fetch), \
                patch('migration_contract.subprocess.check_output', return_value='a'*40+'\n'):
            self.assertEqual(len(migration_ci.prepare(release['tag_name'], Path(directory), 'gap3', 'a'*40)), 3)
            source = json.loads((Path(directory)/'source-contract.json').read_text())
            self.assertEqual(source['checkout_sha'], 'a'*40); self.assertEqual(source['registry'], 'gap3')

    def test_registration_locks_roster_coverage_and_abi_bytes(self):
        registry = ROOT/'tools/migration-contracts.json'
        for kind in ('roster', 'coverage', 'image'):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory); (root/'tools').mkdir(); (root/'deploy').mkdir()
                shutil.copyfile(registry, root/'tools/migration-contracts.json')
                definition = load_profile('gap3')['definition']
                for lock in definition['files'].values(): shutil.copyfile(ROOT/lock['path'], root/lock['path'])
                target = root/definition['files'][kind]['path']; target.write_bytes(target.read_bytes()+b' ')
                with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, 'SHA mismatch'):
                    load_profile('gap3', root)

    def test_wrong_roster_coverage_image_part_and_source_url_are_rejected(self):
        for fault in ('roster', 'coverage', 'image', 'part', 'url'):
            release, ready = fixture(); profile = load_profile('gap3')
            if fault == 'roster': profile['roster'] = load_profile('original61')['roster']
            if fault == 'coverage': profile['coverage']['features'][0]['properties']['name'] = 'changed'
            if fault == 'image': profile['image'] = 'valhalla/valhalla@sha256:'+'0'*64
            part = next(a for a in release['assets'] if '.tar-' in a['name'])
            if fault == 'part': part['digest'] = 'sha256:'+'0'*64
            if fault == 'url': part['browser_download_url'] = 'https://example.invalid/part'
            with self.subTest(fault=fault), self.assertRaises(ValueError):
                validate_profile_supply(profile, release, ready)

    def test_127_of_129_draft_and_missing_ready_are_never_migratable(self):
        profile = load_profile('additions129')
        release = dict(tag_name='tiles-incomplete', draft=True, prerelease=False, assets=[])
        for row in profile['roster']['region'][:127]:
            release['assets'].append(dict(name='tiles-'+row['slug']+'.tar-00', size=1,
                digest='sha256:'+'a'*64, browser_download_url='https://example.invalid/part'))
        with self.assertRaisesRegex(ValueError, 'draft'): validate_profile_supply(profile, release, b'')
        release['draft'] = False
        with self.assertRaisesRegex(ValueError, 'missing READY'): validate_profile_supply(profile, release, b'')
        release['assets'].append(dict(name='READY', size=3))
        with self.assertRaisesRegex(ValueError, 'roster'): validate_profile_supply(profile, release, b'ok\n')

    def test_inventory_graph_mismatch_stops_before_any_tile_upload(self):
        release, ready = fixture()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root/'release.json').write_text(json.dumps(release)); (root/'READY').write_bytes(ready)
            argv = ['migrate', '--release-json', str(root/'release.json'), '--ready', str(root/'READY'),
                    '--work', str(root/'work'), '--region', 'central-america', '--contract', 'gap3', '--source-sha', 'a'*40]
            with patch.object(sys, 'argv', argv), patch('migration_contract.subprocess.check_output', return_value='a'*40+'\n'), \
                    patch.object(cli, 'connection', return_value=(object(), 'test-bucket')), \
                    patch.object(cli, 'Publisher') as publisher, patch.object(cli, 'migrate') as transfer, \
                    patch.object(cli, 'inventory', return_value=({'0/000.gph': {'sha256': '0'*64}}, {})):
                with self.assertRaisesRegex(ValueError, 'READY graph fingerprint'): cli.main()
                transfer.assert_not_called(); publisher.return_value.put.assert_not_called()

    def test_full_cli_cannot_consume_pilot_from_different_checkout(self):
        release, ready = fixture(); profile = load_profile('gap3')
        plans = validate_profile_supply(profile, release, ready)
        with patch('migration_contract.subprocess.check_output', return_value='b'*40+'\n'):
            wrong = source_contract(profile, 'b'*40)
        pilot = dict(schema=1, manifest_published=False, bucket='test-bucket', verified_tiles=20,
                     contract=migration_identity(release, profile['image'], profile['coverage'], plans, wrong))
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); (root/'release.json').write_text(json.dumps(release)); (root/'READY').write_bytes(ready)
            (root/'pilot.json').write_text(json.dumps(pilot))
            argv=['migrate', '--release-json', str(root/'release.json'), '--ready', str(root/'READY'),
                  '--work', str(root/'work'), '--region', 'central-america', '--contract', 'gap3',
                  '--source-sha', 'a'*40, '--mode', 'full', '--pilot-receipt', str(root/'pilot.json')]
            with patch.object(sys, 'argv', argv), patch('migration_contract.subprocess.check_output', return_value='a'*40+'\n'), \
                    patch.object(cli, 'connection', return_value=(object(), 'test-bucket')), patch.object(cli, 'inventory') as inventory:
                with self.assertRaisesRegex(ValueError, 'pilot receipt differs'): cli.main()
                inventory.assert_not_called()

    def test_pilot_contract_cannot_cross_checkout_or_registration(self):
        release, ready = fixture(); profile = load_profile('gap3')
        plans = validate_profile_supply(profile, release, ready)
        with patch('migration_contract.subprocess.check_output', return_value='a'*40+'\n'):
            bound = source_contract(profile, 'a'*40)
            with self.assertRaisesRegex(ValueError, 'checkout differs'): source_contract(profile, 'b'*40)
        original = migration_identity(release, profile['image'], profile['coverage'], plans, bound)
        for key, value in [('checkout_sha', 'b'*40), ('definition_sha256', '0'*64), ('registry', 'original61')]:
            changed = dict(bound, **{key: value})
            self.assertNotEqual(original, migration_identity(release, profile['image'], profile['coverage'], plans, changed))


if __name__ == '__main__': unittest.main()
