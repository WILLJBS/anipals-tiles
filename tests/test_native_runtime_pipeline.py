"""Offline contract/byte-boundary tests; no native process or external service."""
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'tools'),str(ROOT/'deploy')]
from native_runtime_contract import validate,LockedMirror,verified_catalog
from native_runtime_source import SourceDownload
from native_runtime_fixture import request,Store,blob
from native_runtime_prepare import execute
from navigation_catalog_inputs import decode
from runtime_index_assembler import canonical_bytes
from catalog_fixture import Fixture
from navigation_catalog import plan


class ContractTests(unittest.TestCase):
    def test_strict_request_rejects_paths_commands_duplicates_and_invalid_budgets(self):
        value=request();self.assertEqual(validate(value,'a'*40),value)
        for field,wrong in [('source_sha','b'*40),('slug','../volume'),('schema','production'),('volume','/data')]:
            changed=dict(value,**{field:wrong})
            with self.subTest(field=field),self.assertRaises(ValueError):validate(changed,'a'*40)
        for name in value['budgets']:
            for bad in (True,0,-1,2**70):
                changed=copy.deepcopy(value);changed['budgets'][name]=bad
                with self.subTest(name=name,bad=bad),self.assertRaises(ValueError):validate(changed,'a'*40)
        changed=copy.deepcopy(value);changed['objects'].append(changed['objects'][0])
        with self.assertRaisesRegex(ValueError,'DUPLICATE'):validate(changed,'a'*40)
        changed=copy.deepcopy(value);changed['objects'][0]['key']='../../private'
        with self.assertRaisesRegex(ValueError,'NAMESPACE'):validate(changed,'a'*40)
        changed=copy.deepcopy(value);changed['budgets']['metadata_bytes']=1
        with self.assertRaisesRegex(ValueError,'METADATA_BUDGET'):validate(changed,'a'*40)

    def test_mirror_rejects_unlisted_and_extra_tree_objects_before_success(self):
        with tempfile.TemporaryDirectory() as temp:
            store=Store();mirror=LockedMirror(store,Path(temp)/'mirror',request())
            with self.assertRaisesRegex(ValueError,'UNDECLARED'):mirror.read(blob(b'unknown'),100)
            with self.assertRaisesRegex(ValueError,'INCOMPLETE_OBJECT_TREE'):mirror.complete()
            self.assertEqual(store.fetched,[])

    def test_repeated_metadata_reads_are_local_and_corruption_never_refetches(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'source';source.mkdir();spec=request()
            ref=spec['catalog'];(source/ref['sha256']).write_bytes(b'catalog')
            store=Store(source);mirror=LockedMirror(store,root/'mirror',spec)
            self.assertEqual(mirror.read(ref,100),b'catalog')
            self.assertEqual(mirror.read(ref,100),b'catalog')
            self.assertEqual(len(store.fetched),1)
            (mirror.root/ref['sha256']).write_bytes(b'corrupt')
            with self.assertRaises(ValueError):mirror.read(ref,100)
            self.assertEqual(len(store.fetched),1)

    def test_existing_work_directory_is_not_an_isolation_attestation(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);(folder/'production-label').write_text('isolated')
            with self.assertRaisesRegex(ValueError,'FRESH_OWNED'):
                execute(request(),'f'*64,Store(),folder,dict(runnerSourceSha='a'*40))


class CatalogTreeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.root=Path(cls.temp.name);cls.mirror=cls.root/'source';cls.mirror.mkdir()
        cls.f=Fixture(cls.mirror,('original61',))
        source=canonical_bytes(cls.f.request);source_ref=cls.f.put(source)
        catalog=plan(source,source_ref['sha256'],cls.f.objects)
        tree=[s[k] for s in cls.f.sources for k in ('release','ready')]
        tree += [r[k] for r in cls.f.receipts for k in ('receipt','manifest')]
        cls.spec=dict(request(),catalog=cls.f.put(catalog),catalog_request=source_ref,
                      probe=cls.f.put(dict(schema=1)),objects=tree)
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()
    def mirror_for(self,spec):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        return LockedMirror(Store(self.mirror),Path(folder.name)/'mirror',spec)
    def test_full_original_group_is_checked_before_selecting_one_region(self):
        spec=validate(self.spec,'a'*40);objects=self.mirror_for(spec)
        catalog,_=verified_catalog(spec,objects,ROOT)
        self.assertEqual(len(catalog['regions']),61);self.assertFalse(catalog['supply_complete'])
        self.assertEqual(objects.used,set(objects.allowed))
    def test_missing_receipt_in_source_cannot_be_relabelled_single_region(self):
        spec=copy.deepcopy(self.spec);source=copy.deepcopy(self.f.request);source['receipts'].pop()
        spec['catalog_request']=self.f.put(source)
        with self.assertRaisesRegex(ValueError,'COMPLETE_SELECTED_RECEIPT'):
            verified_catalog(spec,self.mirror_for(spec),ROOT)
    def test_unused_or_missing_descriptor_refuses_the_tree(self):
        for remove in (True,False):
            spec=copy.deepcopy(self.spec)
            if remove:spec['objects'].pop()
            else:spec['objects'].append(self.f.put(b'unknown'))
            with self.subTest(remove=remove),self.assertRaisesRegex(ValueError,'UNDECLARED|INCOMPLETE_OBJECT_TREE'):
                verified_catalog(spec,self.mirror_for(spec),ROOT)


class DownloadTests(unittest.TestCase):
    def part(self,data=b'abc'):
        return dict(size=len(data),sha256=hashlib.sha256(data).hexdigest(),url='https://synthetic.invalid/part')
    def test_full_body_sha_and_actual_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'part';downloader=SourceDownload(4,lambda *_args,**_kw:io.BytesIO(b'abc'))
            downloader(path,self.part());self.assertEqual(path.read_bytes(),b'abc')
            self.assertEqual((downloader.actual,downloader.reserved),(3,4))
    def test_oversized_or_wrong_sha_never_leaves_a_candidate(self):
        for body in (b'abcd',b'xyz',b'ab'):
            with self.subTest(body=body),tempfile.TemporaryDirectory() as temp:
                path=Path(temp)/'part';downloader=SourceDownload(4,lambda *_args,**_kw:io.BytesIO(body))
                with self.assertRaises(ValueError):downloader(path,self.part())
                self.assertFalse(path.exists())
    def test_stream_retry_charges_failed_attempt_and_exhaustion_prevents_io(self):
        class Broken(io.BytesIO):
            def read(self,n):raise OSError('synthetic stream failure')
        bodies=[Broken(b'abc'),io.BytesIO(b'abc')];calls=[]
        def opener(*_,**__):calls.append(1);return bodies.pop(0)
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'part';downloader=SourceDownload(8,opener,lambda _:None)
            downloader(path,self.part());self.assertEqual(downloader.reserved,8);self.assertEqual(len(calls),2)
            with self.assertRaisesRegex(ValueError,'BUDGET_EXHAUSTED'):downloader(path,self.part())
            self.assertEqual(len(calls),2)
    def test_concurrent_reservations_have_one_shared_atomic_limit(self):
        from concurrent.futures import ThreadPoolExecutor
        import threading
        budget=SourceDownload(33);barrier=threading.Barrier(8)
        def reserve(_):
            barrier.wait()
            try:budget.reserve(11);return True
            except ValueError:return False
        with ThreadPoolExecutor(max_workers=8) as pool:accepted=list(pool.map(reserve,range(8)))
        self.assertEqual(sum(accepted),3);self.assertEqual(budget.reserved,33)

    def test_timeout_exhausts_three_attempts_and_no_more(self):
        calls=[]
        def opener(*_,**__):calls.append(1);raise TimeoutError('synthetic')
        with tempfile.TemporaryDirectory() as temp:
            downloader=SourceDownload(100,opener,lambda _:None)
            with self.assertRaises(TimeoutError):downloader(Path(temp)/'part',self.part())
            self.assertEqual(len(calls),3);self.assertEqual(downloader.reserved,12)


if __name__=='__main__':unittest.main()
