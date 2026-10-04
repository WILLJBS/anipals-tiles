"""Synthetic tar/engine tests exercise actual local extraction and inventory only."""
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'tools'),str(ROOT/'deploy')]
from native_runtime_prepare import materialize
from native_runtime_source import SourceDownload,bounded_tiles
from native_runtime_fixture import request
from migration_contract import load_profile
from regional_engine import EngineError


def archive():
    buffer=io.BytesIO()
    with tarfile.open(fileobj=buffer,mode='w') as tar:
        info=tarfile.TarInfo('tiles/2/000/001.gph');info.size=3
        tar.addfile(info,io.BytesIO(b'abc'))
    return buffer.getvalue()


class MaterializeTests(unittest.TestCase):
    def fixture(self,temp):
        root=Path(temp).resolve();objects=root/'objects';objects.mkdir()
        image=load_profile('original61')['image'];body=archive();slug='europe-germany'
        plan=dict(slug=slug,fingerprint='a'*64,image=image,release='tiles-synthetic',parts=[
            dict(name='tiles-europe-germany.tar-00',url='https://synthetic.invalid/part',
                 size=len(body),sha256=hashlib.sha256(body).hexdigest())])
        spec=dict(request(),_request_sha='b'*64)
        catalog=dict(image=image,sources=[dict(registry='original61',release={},ready={})])
        mirror=SimpleNamespace(root=objects,read=lambda *_:b'{}')
        return root,body,plan,spec,catalog,mirror
    def test_actual_extraction_marker_hash_inventory_and_native_activation_order(self):
        with tempfile.TemporaryDirectory() as temp:
            root,body,plan,spec,catalog,mirror=self.fixture(temp)
            engine=SimpleNamespace(request=lambda *_args,**_kw:[dict(edges=[{}])])
            with patch('native_runtime_prepare.validate_profile_supply',return_value=[plan]),patch(
                    'regional_download.validate_tiles',return_value=dict(tiles=1)),patch(
                    'native_runtime_prepare.native_engine',return_value=engine):
                volume,identity,inventory,ref,proof,_=materialize(spec,catalog,mirror,root,{},
                    lambda path,_:path.write_bytes(body))
            self.assertTrue(proof['verified']);self.assertEqual(inventory['regions'][0]['tile_bytes'],3)
            self.assertEqual(inventory['regions'][0]['fingerprint'],plan['fingerprint'])
            self.assertEqual(ref['sha256'],hashlib.sha256((mirror.root/ref['sha256']).read_bytes()).hexdigest())
            self.assertTrue((volume/'regions/europe-germany/active.json').exists())
            self.assertFalse(any((volume/'regions/.downloads').glob('*/*/part-*')))
    def test_source_sha_failure_occurs_before_native_and_never_activates(self):
        with tempfile.TemporaryDirectory() as temp:
            root,body,plan,spec,catalog,mirror=self.fixture(temp)
            with patch('native_runtime_prepare.validate_profile_supply',return_value=[plan]),patch(
                    'native_runtime_prepare.native_engine') as engine:
                with self.assertRaisesRegex(ValueError,'exact digest'):
                    materialize(spec,catalog,mirror,root,{},lambda path,_:path.write_bytes(b'wrong'))
            engine.assert_not_called();self.assertFalse(list(root.rglob('active.json')))
    def test_native_failure_never_creates_active_pointer_or_inventory_blob(self):
        with tempfile.TemporaryDirectory() as temp:
            root,body,plan,spec,catalog,mirror=self.fixture(temp)
            def fail(*_,**__):raise EngineError('synthetic native failure')
            engine=SimpleNamespace(request=fail)
            with patch('native_runtime_prepare.validate_profile_supply',return_value=[plan]),patch(
                    'regional_download.validate_tiles',return_value=dict(tiles=1)),patch(
                    'native_runtime_prepare.native_engine',return_value=engine):
                with self.assertRaises(EngineError):materialize(spec,catalog,mirror,root,{},lambda path,_:path.write_bytes(body))
            self.assertFalse(list(root.rglob('active.json')));self.assertEqual(list(mirror.root.iterdir()),[])
    def test_archive_budget_rejects_before_creating_volume(self):
        with tempfile.TemporaryDirectory() as temp:
            root,body,plan,spec,catalog,mirror=self.fixture(temp);spec['budgets']['archive_bytes']=1
            with patch('native_runtime_prepare.validate_profile_supply',return_value=[plan]):
                with self.assertRaisesRegex(ValueError,'ARCHIVE_BUDGET'):
                    materialize(spec,catalog,mirror,root,{},lambda *_:self.fail('download'))
            self.assertFalse((root/'volume').exists())


class ReaderBudgetTests(unittest.TestCase):
    def test_budget_is_reserved_before_source_and_unknown_key_never_reads(self):
        calls=[]
        def fetch(key):calls.append(key);yield b'abc'
        budget=SourceDownload(3*(3+1024**2))
        read=bounded_tiles(fetch,'prefix/',{'tile':dict(size=3)},budget)
        self.assertEqual(list(read('prefix/tile')),[b'abc']);self.assertEqual(budget.actual,3)
        with self.assertRaisesRegex(ValueError,'BUDGET'):list(read('prefix/tile'))
        with self.assertRaisesRegex(ValueError,'CROSS_GRAPH'):list(read('elsewhere/tile'))
        self.assertEqual(calls,['prefix/tile'])
    def test_oversize_and_early_close_always_release_source(self):
        closed=[]
        def fetch(_):
            try:yield b'abc';yield b'extra'
            finally:closed.append(True)
        budget=SourceDownload(20*1024**2);read=bounded_tiles(fetch,'p/',{'t':dict(size=3)},budget)
        with self.assertRaisesRegex(ValueError,'MANIFEST_SIZE'):list(read('p/t'))
        stream=read('p/t');self.assertEqual(next(stream),b'abc');stream.close()
        self.assertEqual(closed,[True,True]);self.assertLessEqual(budget.actual,budget.reserved)


if __name__=='__main__':unittest.main()
