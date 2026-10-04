import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from runtime_index_fixture import RuntimeFixture, ROOT, raw, BODY
from runtime_index_assembler import assemble
from runtime_local_inventory import capture


class RuntimeIndexTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.f=RuntimeFixture(cls.temp.name)
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()
    def run_plan(self,request=None):
        data=raw(request or self.f.request)
        return assemble(data,hashlib.sha256(data).hexdigest(),self.f.fixture.objects,self.f.volume)
    def changed(self):return copy.deepcopy(self.f.request)
    def test_isolated_scope_is_exact_and_never_global_completion(self):
        result=self.run_plan();self.assertEqual(result['selected_regions'],['europe-germany'])
        self.assertFalse(result['production_supply_complete']);self.assertFalse(result['published'])
        self.assertFalse(result['runtime_activated']);self.assertTrue(result['local_rollback_sha_verified'])
        self.assertEqual(len(result['index']['storage_ownership']),1)
        self.assertEqual(len(result['coverage']['features']),1)
        self.assertEqual(result['index']['catalog_sha256'],self.f.catalog_ref['sha256'])
        self.assertEqual(result['index']['storage_ownership']['europe-germany']['rollback']['fingerprint'],self.f.local_plans['europe-germany']['fingerprint'])
    def test_partial_catalog_and_subset_cannot_be_called_production(self):
        request=self.changed();request['mode']='production-candidate'
        with self.assertRaisesRegex(ValueError,'COMPLETE_193'):self.run_plan(request)
    def test_wrong_request_catalog_and_source_bytes_rejected(self):
        data=raw(self.f.request)
        with self.assertRaises(ValueError):assemble(data,'0'*64,self.f.fixture.objects,self.f.volume)
        for key in ('catalog','catalog_request','target_inventory'):
            request=self.changed();request[key]['sha256']='0'*64
            with self.subTest(key=key),self.assertRaises(ValueError):self.run_plan(request)
        request=self.changed();bad=copy.deepcopy(self.f.catalog);bad['supply_complete']=True
        request['catalog']=self.f.fixture.put(bad)
        with self.assertRaisesRegex(ValueError,'REVERIFIED'):self.run_plan(request)
    def test_source_missing_and_feature_image_fingerprint_catalog_tampering_rejected(self):
        for field in ('sources','feature','image','graph'):
            request=self.changed();catalog=copy.deepcopy(self.f.catalog)
            if field=='sources':catalog['sources']=[]
            if field=='feature':catalog['regions'][0]['feature']['properties']['tampered']=True
            if field=='image':catalog['image']='valhalla/valhalla@sha256:'+'f'*64
            if field=='graph':catalog['regions'][0]['graph_fingerprint']='f'*64
            request['catalog']=self.f.fixture.put(catalog)
            with self.subTest(field=field),self.assertRaisesRegex(ValueError,'REVERIFIED'):self.run_plan(request)
    def test_incomplete_source_request_and_previous_index_cannot_be_relabelled(self):
        request=self.changed();source=copy.deepcopy(self.f.fixture.request);source['receipts'].pop()
        request['catalog_request']=self.f.fixture.put(source)
        with self.assertRaisesRegex(ValueError,'COMPLETE_SELECTED'):self.run_plan(request)
        request=self.changed();request['previous_index_sha256']='f'*64
        with self.assertRaisesRegex(ValueError,'PREVIOUS_INDEX'):self.run_plan(request)

    def test_duplicates_unknowns_path_injection_and_probe_gaps_rejected(self):
        for values in ([],['europe-germany','europe-germany'],['../europe-germany'],['$(touch injected)'],['missing'],[True]):
            request=self.changed();request['selected_regions']=values
            with self.subTest(values=values),self.assertRaises(ValueError):self.run_plan(request)
        request=self.changed();request['probes']={}
        with self.assertRaisesRegex(ValueError,'PROBE_ROSTER'):self.run_plan(request)
        request=self.changed();request['probes']['europe-germany']=[dict(lat=0,lng=0)]
        with self.assertRaisesRegex(ValueError,'outside'):self.run_plan(request)
    def test_target_identity_and_live_same_size_byte_drift_rejected(self):
        request=self.changed();request['target_identity_sha256']='2'*64
        with self.assertRaisesRegex(ValueError,'INVENTORY_CHANGED'):self.run_plan(request)
        fp=self.f.local_plans['europe-germany']['fingerprint'];tile=self.f.volume/'regions/europe-germany'/fp/'tiles/2/000/001.gph'
        tile.write_bytes(b'xyz')
        try:
            with self.assertRaisesRegex(ValueError,'INVENTORY_CHANGED'):self.run_plan()
            observed=capture(self.f.volume,['europe-germany'],self.f.image,'1'*64)
            request=self.changed();request['target_inventory']=self.f.fixture.put(observed)
            with self.assertRaisesRegex(ValueError,'TILE_SHA_DIFFERS'):self.run_plan(request)
        finally:tile.write_bytes(BODY)
    def test_missing_or_corrupt_marker_identity_and_tile_do_not_create_inventory(self):
        fp=self.f.local_plans['europe-germany']['fingerprint'];folder=self.f.volume/'regions/europe-germany'/fp
        for name in ('.complete.json','.identity.json','tiles/2/000/001.gph'):
            path=folder/name;data=path.read_bytes();path.unlink()
            try:
                with self.subTest(name=name),self.assertRaises((FileNotFoundError,ValueError)):
                    capture(self.f.volume,['europe-germany'],self.f.image,'1'*64)
            finally:path.write_bytes(data)
        marker=folder/'.complete.json';original=marker.read_bytes();bad=json.loads(original);bad['fingerprint']='e'*64
        marker.write_bytes(raw(bad))
        try:
            with self.assertRaisesRegex(ValueError,'MARKER_IDENTITY'):capture(self.f.volume,['europe-germany'],self.f.image,'1'*64)
        finally:marker.write_bytes(original)
        identity=folder/'.identity.json';original=identity.read_bytes();bad=json.loads(original);bad['image']='wrong'
        identity.write_bytes(raw(bad))
        try:
            with self.assertRaisesRegex(ValueError,'IMAGE'):capture(self.f.volume,['europe-germany'],self.f.image,'1'*64)
        finally:identity.write_bytes(original)
    def test_symlink_volume_and_duplicate_json_fields_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            link=Path(directory)/'link';link.symlink_to(self.f.volume,target_is_directory=True)
            with self.assertRaises(ValueError):capture(link,['europe-germany'],self.f.image,'1'*64)
        data=raw(self.f.request)[:-1]+b',"mode":"production-candidate"}'
        with self.assertRaisesRegex(ValueError,'DUPLICATE_JSON'):
            assemble(data,hashlib.sha256(data).hexdigest(),self.f.fixture.objects,self.f.volume)
    def test_inventory_reads_existing_files_without_creating_locks(self):
        before={p.relative_to(self.f.volume).as_posix():p.read_bytes() for p in self.f.volume.rglob('*') if p.is_file()}
        capture(self.f.volume,['europe-germany'],self.f.image,'1'*64)
        after={p.relative_to(self.f.volume).as_posix():p.read_bytes() for p in self.f.volume.rglob('*') if p.is_file()}
        self.assertEqual(after,before)
    def test_cli_private_output_no_overwrite_and_no_activation(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);request=root/'request.json';request.write_bytes(raw(self.f.request));output=root/'review'
            args=[sys.executable,str(ROOT/'tools/plan_runtime_index.py'),'assemble','--request',str(request),
                  '--request-sha',hashlib.sha256(request.read_bytes()).hexdigest(),'--objects',str(self.f.mirror),
                  '--volume-root',str(self.f.volume),'--output-directory',str(output)]
            run=subprocess.run(args,capture_output=True,text=True);self.assertEqual(run.returncode,0,run.stderr)
            self.assertEqual(output.stat().st_mode&0o777,0o700)
            self.assertEqual({p.name for p in output.iterdir()},{'index.json','coverage.json','review.json'})
            self.assertTrue(all(p.stat().st_mode&0o777==0o600 for p in output.iterdir()))
            self.assertNotEqual(subprocess.run(args,capture_output=True).returncode,0)
            self.assertFalse(list(self.f.volume.rglob('ownership.json')))
            self.assertNotIn('fixture-private-bucket',run.stdout)
    def test_all_193_production_supply_still_requires_native_runtime_acceptance(self):
        with tempfile.TemporaryDirectory() as folder:
            f=RuntimeFixture(folder,complete=True);data=raw(f.request)
            result=assemble(data,hashlib.sha256(data).hexdigest(),f.fixture.objects,f.volume)
            self.assertEqual(len(result['selected_regions']),193);self.assertTrue(result['production_supply_complete'])
            self.assertEqual(len(result['index']['storage_ownership']),193)
            self.assertEqual(len(result['coverage']['features']),61)
            self.assertFalse(result['native_rollback_verified']);self.assertFalse(result['runtime_activated'])
            f.request['selected_regions'].pop();data=raw(f.request)
            with self.assertRaisesRegex(ValueError,'COMPLETE_193'):
                assemble(data,hashlib.sha256(data).hexdigest(),f.fixture.objects,f.volume)


if __name__=='__main__':unittest.main()
