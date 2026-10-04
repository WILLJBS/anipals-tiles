"""Offline full-roster and cross-run rejection gates; no external IO."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from catalog_fixture import Fixture, ROOT, raw
from navigation_catalog import plan


class CatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.fixture = Fixture(cls.temp.name)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def run_plan(self, request=None):
        data = raw(self.fixture.request if request is None else request)
        return plan(data, hashlib.sha256(data).hexdigest(), self.fixture.objects)

    def changed(self):
        return copy.deepcopy(self.fixture.request)

    def test_old61_and_gap3_cross_revision_preserved_not_activation(self):
        result = self.run_plan()
        self.assertEqual(result['counts'], dict(expected_regions=193, catalog_regions=64,
            deferred_regions=129, expected_source_scopes=6222, source_scopes_in_catalog=5102))
        self.assertEqual({r['registered_source']['checkout_sha'] for r in result['regions']}, {'a'*40, 'b'*40})
        self.assertEqual(len(result['deferred_contracts'][0]['slugs']), 129)
        for key in ('supply_complete', 'runtime_activated', 'scope_routes_verified', 'tile_bytes_reverified', 'runtime_ownership_reviewed'):
            self.assertIs(result[key], False)
        for source, row in zip(sorted(self.fixture.receipts, key=lambda r:r['slug']), result['regions']):
            original = json.loads((Path(self.temp.name)/source['receipt']['sha256']).read_bytes())
            self.assertEqual(row['registered_source'], original['registered_source'])
            self.assertEqual(row['contract'], original['contract'])

    def test_request_sha_and_definition_registry_scope_locks(self):
        with self.assertRaises(ValueError): plan(raw(self.fixture.request), '0'*64, self.fixture.objects)
        for field in ('registry_sha256', 'scope_sha256'):
            request=self.changed(); request[field]='0'*64
            with self.subTest(field=field), self.assertRaises(ValueError): self.run_plan(request)
        request=self.changed();request['sources'][0]['definition_sha256']='0'*64
        with self.assertRaisesRegex(ValueError, 'DEFINITION'): self.run_plan(request)

    def test_missing_duplicate_and_deferred_group_must_be_explicit(self):
        for change in ('missing', 'duplicate', 'omit_group', 'overlap', 'missing_allowlist'):
            request=self.changed()
            if change=='missing': request['receipts'].pop()
            if change=='duplicate': request['receipts'][-1]=request['receipts'][0]
            if change=='omit_group': request['deferred_contracts']=[]
            if change=='overlap': request['deferred_contracts'].append('gap3')
            if change=='missing_allowlist': request['sources'][0]['allowed_runner_shas']=[]
            with self.subTest(change=change), self.assertRaises(ValueError): self.run_plan(request)

    def test_unknown_runner_and_relabelled_old_receipt_rejected(self):
        for change in ('runner', 'relabel', 'definition', 'contract', 'bucket', 'feature'):
            request=self.changed();item=request['receipts'][0]
            if change=='runner': item['runner_sha']='c'*40
            else:
                value=json.loads((Path(self.temp.name)/item['receipt']['sha256']).read_bytes())
                if change=='relabel': value['registered_source']['checkout_sha']='a'*40
                if change=='definition': value['registered_source']['definition_sha256']='0'*64
                if change=='contract': value['contract']='0'*64
                if change=='bucket': value['bucket']='other-bucket'
                if change=='feature': value['feature']['properties']['name']='substituted'
                item['receipt']=self.fixture.replace(item['receipt'], value)
            with self.subTest(change=change), self.assertRaises(ValueError): self.run_plan(request)

    def test_incomplete_129_cannot_be_selected_or_pass_as_ready(self):
        request=self.changed();request['sources'].append(dict(request['sources'][0],registry='additions129'))
        request['deferred_contracts']=[]
        with self.assertRaises(ValueError):self.run_plan(request)
        request=self.changed();source=request['sources'][1]
        release=copy.deepcopy(self.fixture.group_data['gap3']['release']);release['draft']=True
        source['release']=self.fixture.put(release)
        with self.assertRaisesRegex(ValueError,'draft'):self.run_plan(request)

    def test_exact_object_identity_size_sha_and_pilot_rejected(self):
        for change in ('receipt_key','receipt_size','manifest_key','manifest_size','pilot'):
            request=self.changed();item=request['receipts'][0]
            if change=='receipt_key':item['receipt']['key']=item['receipt']['key'].replace('b'*40,'a'*40)
            if change=='receipt_size':item['receipt']['bytes']+=1
            if change=='manifest_key':item['manifest']['key']=item['manifest']['key'].replace('/graphs/','/other/')
            if change=='manifest_size':item['manifest']['bytes']=True
            if change=='pilot':item['receipt']=self.fixture.replace(item['receipt'],dict(schema=1,verified_tiles=20,manifest_published=False))
            with self.subTest(change=change),self.assertRaises(ValueError):self.run_plan(request)

    def test_manifest_abi_graph_source_and_native_proof_tampering(self):
        for change in ('abi','graph','source','native','size'):
            request=self.changed();item=next(i for i in request['receipts'] if i['registry']=='gap3')
            value=json.loads((Path(self.temp.name)/item['manifest']['sha256']).read_bytes())
            if change=='abi':value['image']='valhalla/valhalla@sha256:'+'0'*64
            if change=='graph':value['graph_fingerprint']='0'*64
            if change=='source':value['source']['parts'][0]['sha256']='0'*64
            if change=='native':value['native_validation']['probes'][0]['distance_km']=0.2
            item['manifest']=self.fixture.replace(item['manifest'],value)
            receipt=json.loads((Path(self.temp.name)/item['receipt']['sha256']).read_bytes())
            receipt.update(manifest_sha256=item['manifest']['sha256'],manifest_size=item['manifest']['bytes']+(change=='size'))
            item['receipt']=self.fixture.replace(item['receipt'],receipt)
            with self.subTest(change=change),self.assertRaises(ValueError):self.run_plan(request)

    def test_complete_193_keeps_all_additions_and_still_not_routes(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture=Fixture(directory, ('original61','gap3','additions129'))
            data=raw(fixture.request);result=plan(data,hashlib.sha256(data).hexdigest(),fixture.objects)
            self.assertEqual(result['counts']['catalog_regions'],193)
            self.assertEqual(result['counts']['source_scopes_in_catalog'],6222)
            self.assertTrue(result['supply_complete']);self.assertFalse(result['scope_routes_verified'])
            fixture.request['receipts'].pop();fixture.request['receipts'].pop()
            data=raw(fixture.request)
            with self.assertRaisesRegex(ValueError,'COMPLETE_SELECTED'):
                plan(data,hashlib.sha256(data).hexdigest(),fixture.objects)

    def test_cli_local_private_output_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            request=Path(directory)/'request.json';request.write_bytes(raw(self.fixture.request))
            output=Path(directory)/'catalog.json';digest=hashlib.sha256(request.read_bytes()).hexdigest()
            args=[sys.executable,str(ROOT/'tools/plan_navigation_catalog.py'),'--request',str(request),
                  '--request-sha',digest,'--objects',self.temp.name,'--output',str(output)]
            result=subprocess.run(args,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(output.stat().st_mode&0o777,0o600)
            self.assertNotIn('fixture-private-bucket',result.stdout)
            self.assertNotIn('migration-receipts',result.stdout)
            self.assertNotEqual(subprocess.run(args,capture_output=True).returncode,0)


if __name__=='__main__':unittest.main()
