import copy
import fcntl
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'deploy'));sys.path.insert(0,str(ROOT/'tools'))
import regional_gc as gc
import regional_ownership as ownership
from regional_catalog import Catalog
from regional_composite import Composite
from regional_download import activate_region, run_pass
from regional_release import canonical_hash
from test_regional_composite import feature
from test_regional_objects import IMAGE, inventory


def fixture():
    shape=feature('local',0);base=dict(type='FeatureCollection',features=[shape])
    manifest=inventory('local',b'good');manifest['coverage_sha256']=canonical_hash(shape)
    raw=json.dumps(manifest).encode();digest=hashlib.sha256(raw).hexdigest()
    row=dict(slug='local',graph_fingerprint=manifest['graph_fingerprint'],feature=shape,
             manifest_sha256=digest,manifest_size=len(raw),probes=[dict(lat=.5,lng=.5)])
    owner=dict(selected=dict(storage='r2',fingerprint=canonical_hash(dict(manifest_sha256=digest,image=IMAGE))),
               rollback=dict(storage='local',fingerprint='a'*64))
    index=dict(schema=2,image=IMAGE,local_coverage_sha256=canonical_hash(base),remote_regions=[row],
               catalog_sha256='c'*64,previous_index_sha256=None,storage_ownership={'local':owner})
    return base,index,raw


def load(base,index,manifest):
    raw=json.dumps(index).encode()
    return Composite(raw,hashlib.sha256(raw).hexdigest(),base,IMAGE,{'local':manifest})


class OwnershipTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.parent=self.root/'regions/local'
        self.base,self.index,self.manifest=fixture();self.composite=load(self.base,self.index,self.manifest)
        tiles=self.parent/('a'*64)/'tiles';tiles.mkdir(parents=True)
        self.local=dict(slug='local',fingerprint='a'*64,release='local-source',tile_dir=str(tiles.relative_to(self.root)),tile_count=1)
        (tiles.parent/'.complete.json').write_text(json.dumps(self.local))
        gc.activate(self.root,self.local)
    def activate_remote(self):
        self.composite.install_ownership(self.root)
        descriptor=self.composite.prepare(self.root,'local')
        activate_region(self.root,descriptor,dict(slug='local',fingerprint=descriptor['fingerprint'],verified=True),self.composite.digest)
        return descriptor
    def test_v2_replaces_baseline_without_duplicate_feature_and_stale_local_cannot_win(self):
        remote=self.activate_remote()
        self.assertEqual(self.composite.coverage,self.base)
        with self.assertRaisesRegex(ValueError,'stale worker'):gc.activate(self.root,self.local)
        self.assertEqual(gc.read_pointer(self.parent)['fingerprint'],remote['fingerprint'])
        self.assertEqual(Catalog(self.root,self.base).available()['local']['storage'],'r2')
        with patch('regional_download.prepare_region') as prepare:
            self.assertEqual(run_pass(self.root,[dict(slug='local',fingerprint='a'*64,parts=[])]),[])
            prepare.assert_not_called()
    def test_retained_local_is_not_collected_and_rollback_survives_same_index_restart(self):
        remote=self.activate_remote()
        self.assertEqual(gc.collect_retired(self.root),0)
        self.assertTrue((self.parent/('a'*64)).exists())
        proof=dict(slug='local',fingerprint='a'*64,verified=True)
        ownership.rollback(self.root,'local',self.composite.digest,'a'*64,proof)
        self.composite.install_ownership(self.root)
        self.assertEqual(ownership.read_control(self.parent)['mode'],'rollback')
        self.assertEqual(Catalog(self.root,self.base).available()['local']['fingerprint'],'a'*64)
        with self.assertRaisesRegex(ValueError,'stale worker'):gc.activate(self.root,remote,self.composite.digest)
    def test_failed_activation_keeps_old_pointer_but_never_silently_serves_wrong_owner(self):
        self.composite.install_ownership(self.root)
        remote=self.composite.prepare(self.root,'local')
        with self.assertRaises(ValueError):activate_region(self.root,remote,dict(verified=False),self.composite.digest)
        self.assertEqual(gc.read_pointer(self.parent)['fingerprint'],'a'*64)
        self.assertEqual(Catalog(self.root,self.base).available(),{})
        self.assertTrue((self.parent/('a'*64)).exists())
    def test_stale_remote_token_and_changed_index_require_compare_and_swap(self):
        remote=self.activate_remote()
        with self.assertRaisesRegex(ValueError,'stale remote'):gc.activate(self.root,remote,'f'*64)
        newer=copy.deepcopy(self.index);newer['catalog_sha256']='d'*64
        with self.assertRaisesRegex(ValueError,'compare-and-swap'):load(self.base,newer,self.manifest).install_ownership(self.root)
        newer['previous_index_sha256']=self.composite.digest
        candidate=load(self.base,newer,self.manifest);candidate.install_ownership(self.root)
        with self.assertRaisesRegex(ValueError,'stale remote'):gc.activate(self.root,remote,self.composite.digest)
        gc.activate(self.root,remote,candidate.digest)
    def test_rollback_requires_exact_native_proof_and_preserves_inflight_remote_lease(self):
        remote=self.activate_remote();fingerprint=remote['fingerprint']
        for proof in ({},dict(slug='local',fingerprint='a'*64,verified=False),dict(slug='local',fingerprint='a'*64,verified=1)):
            with self.assertRaises(ValueError):ownership.rollback(self.root,'local',self.composite.digest,'a'*64,proof)
        with gc.file_lock(self.parent/fingerprint/'.lease.lock',fcntl.LOCK_SH):
            ownership.rollback(self.root,'local',self.composite.digest,'a'*64,dict(slug='local',fingerprint='a'*64,verified=True))
            self.assertEqual(gc.collect_retired(self.root),1)
            self.assertTrue((self.parent/fingerprint).exists())
        self.assertEqual(gc.collect_retired(self.root),0)
        self.assertFalse((self.parent/fingerprint).exists())
        self.assertTrue((self.parent/('a'*64)).exists())
    def test_rollback_crash_before_atomic_pointer_write_leaves_remote_selected(self):
        remote=self.activate_remote();original=ownership.atomic_json
        def fail(path,value):
            if path.name=='ownership.json':raise OSError('power loss')
            original(path,value)
        with patch.object(ownership,'atomic_json',side_effect=fail),self.assertRaises(OSError):
            ownership.rollback(self.root,'local',self.composite.digest,'a'*64,dict(slug='local',fingerprint='a'*64,verified=True))
        self.assertEqual(ownership.read_control(self.parent)['mode'],'selected')
        self.assertEqual(gc.read_pointer(self.parent)['fingerprint'],remote['fingerprint'])
        self.assertEqual(gc.collect_retired(self.root),0)
        self.assertTrue((self.parent/remote['fingerprint']).exists())
    def test_unknown_owner_wrong_fp_changed_baseline_and_missing_rollback_fail(self):
        for fault in ('owner','fp','coverage','rollback','catalog'):
            changed=copy.deepcopy(self.index)
            if fault=='owner':changed['storage_ownership']['extra']=changed['storage_ownership']['local']
            if fault=='fp':changed['storage_ownership']['local']['selected']['fingerprint']='f'*64
            if fault=='coverage':changed['remote_regions'][0]['feature']['properties']['new']='changed'
            if fault=='rollback':changed['storage_ownership']['local']['rollback']=None
            if fault=='catalog':changed['catalog_sha256']='wrong'
            with self.subTest(fault=fault),self.assertRaises(ValueError):load(self.base,changed,self.manifest)
    def test_preflight_rejects_missing_rollback_before_any_ownership_mutation(self):
        owners=copy.deepcopy(self.index['storage_ownership']);owners['z-missing']=copy.deepcopy(owners['local'])
        with self.assertRaises(FileNotFoundError):ownership.install_many(self.root,owners,self.composite.digest,None)
        self.assertFalse((self.parent/'ownership.json').exists())
        self.assertEqual(gc.read_pointer(self.parent)['fingerprint'],'a'*64)
    def test_malformed_durable_control_fails_closed(self):
        self.activate_remote();path=self.parent/'ownership.json';original=json.loads(path.read_text())
        for change in ({'schema':True},{'active':{}},{'active':{'fingerprint':'a'*64,'release':None}},
                       {'active':{'fingerprint':'bad','release':'test'}},{'mode':'automatic-fallback'}):
            path.write_text(json.dumps(dict(original,**change)))
            with self.subTest(change=change),self.assertRaises(ValueError):ownership.read_control(self.parent)
        path.write_text(json.dumps(original))
    def test_shared_state_dependency_is_one_direction(self):
        import ast
        def imports(name):
            tree=ast.parse((ROOT/'deploy'/name).read_text())
            return {node.module for node in ast.walk(tree) if isinstance(node,ast.ImportFrom)}
        self.assertNotIn('regional_gc',imports('regional_ownership.py'))
        self.assertNotIn('regional_gc',imports('regional_state.py'))
        self.assertNotIn('regional_ownership',imports('regional_state.py'))

    def test_same_index_restart_rechecks_selected_local_marker(self):
        owner=dict(selected=dict(storage='local',fingerprint='a'*64),rollback=None)
        ownership.install(self.root,'local',owner,self.composite.digest,None)
        marker=self.parent/('a'*64)/'.complete.json';marker.unlink()
        with self.assertRaises(FileNotFoundError):
            ownership.install(self.root,'local',owner,self.composite.digest,None)
        self.assertEqual(gc.read_pointer(self.parent)['fingerprint'],'a'*64)

    def test_same_index_restart_rechecks_retained_marker_without_resetting_rollback(self):
        self.activate_remote()
        ownership.rollback(self.root,'local',self.composite.digest,'a'*64,dict(slug='local',fingerprint='a'*64,verified=True))
        marker=self.parent/('a'*64)/'.complete.json';raw=marker.read_bytes()
        control=(self.parent/'ownership.json').read_bytes()
        for fault in ('missing','corrupt','identity'):
            if fault=='missing':marker.unlink()
            elif fault=='corrupt':marker.write_bytes(b'{broken')
            else:marker.write_text(json.dumps(dict(self.local,fingerprint='b'*64)))
            with self.subTest(fault=fault),self.assertRaises((ValueError,FileNotFoundError)):
                self.composite.install_ownership(self.root)
            self.assertEqual((self.parent/'ownership.json').read_bytes(),control)
            marker.write_bytes(raw)
        self.composite.install_ownership(self.root)
        self.assertEqual(ownership.read_control(self.parent)['mode'],'rollback')

    def test_retention_and_owner_changes_share_the_gc_activation_lock(self):
        from threading import Event, Thread
        import time
        entered=Event();finished=Event();errors=[]
        def install():
            entered.set()
            try:self.composite.install_ownership(self.root)
            except BaseException as error:errors.append(error)
            finished.set()
        with gc.file_lock(self.parent/'.activation.lock',fcntl.LOCK_EX):
            worker=Thread(target=install);worker.start();self.assertTrue(entered.wait(2))
            self.assertFalse(finished.wait(.05),'ownership must await shared activation lock')
        worker.join(3);self.assertTrue(finished.is_set());self.assertEqual(errors,[])
    def test_status_completion_requires_verified_exact_owned_graph_set(self):
        from regional_router import Router
        remote=self.activate_remote();router=Router(Catalog(self.root,self.base),None,[])
        router.ownership_index=self.composite.digest
        self.assertFalse(router.status()['complete'])
        router.mark_verified(remote);self.assertTrue(router.status()['complete'])
        self.assertEqual(router.status()['ownership_index'],self.composite.digest)
    def test_deploy_copy_and_trigger_include_new_shared_runtime_modules(self):
        docker=(ROOT/'deploy/Dockerfile').read_text()
        workflow=(ROOT/'.github/workflows/deploy-image.yml').read_text()
        self.assertIn('COPY deploy/regional_*.py tools/validate_tiles.py /usr/local/lib/anipals/',docker)
        self.assertIn("- 'deploy/**'",workflow)
        for name in ('regional_state.py','regional_ownership.py','regional_rollback.py'):
            self.assertTrue((ROOT/'deploy'/name).is_file())

    def test_partial_index_write_restart_resumes_without_resetting_existing_control(self):
        owner=self.index['storage_ownership']['local'];self.composite.install_ownership(self.root)
        ownership.rollback(self.root,'local',self.composite.digest,'a'*64,dict(slug='local',fingerprint='a'*64,verified=True))
        ownership.install_many(self.root,{'local':owner,'remote-addition':dict(selected=owner['selected'],rollback=None)},self.composite.digest,None)
        self.assertEqual(ownership.read_control(self.parent)['mode'],'rollback')
        self.assertIsNone(ownership.read_control(self.root/'regions/remote-addition')['active'])


if __name__=='__main__':unittest.main()
