import hashlib
import io
import json
import struct
from pathlib import Path
import sys
import tempfile
from threading import Event, Lock, Thread
from concurrent.futures import ThreadPoolExecutor
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'));sys.path.insert(0,str(ROOT/'deploy'))
from migration_queue import UploadQueue, MAX_PENDING_BYTES
from migration_r2 import Publisher
import migration_stream
from migration_stream import inventory, migrate
from test_release_r2_migration import archive, source
from test_regional_download import tile


def item(body): return dict(size=len(body),sha256=hashlib.sha256(body).hexdigest())


class QueueTests(unittest.TestCase):
    def test_overlapping_uploads_keep_exclusive_files_after_current_path_reuse(self):
        started=[Event(),Event()];release=Event();lock=Lock();running=peak=0;received={};paths=[]
        def upload(name,path,record):
            nonlocal running,peak
            with lock:
                running+=1;peak=max(peak,running);paths.append(path)
            if name in ('0','1'):started[int(name)].set()
            if not release.wait(3):raise RuntimeError('test release missing')
            raw=path.read_bytes();self.assertEqual(hashlib.sha256(raw).hexdigest(),record['sha256'])
            with lock:received[name]=raw;running-=1
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);events=[]
            try:
                with UploadQueue(root,upload,workers=2,max_bytes=12,progress=events.append) as queue:
                    for index in range(3):
                        raw=str(index).encode()*4;path=root/'current.gph';path.write_bytes(raw)
                        queue.submit(str(index),path,item(raw))
                    self.assertTrue(all(event.wait(2) for event in started))
                    self.assertEqual(queue.pending_bytes,12)
                    release.set()
            finally:release.set()
            self.assertEqual(received,{'0':b'0000','1':b'1111','2':b'2222'})
            self.assertEqual(peak,2);self.assertEqual(len(set(paths)),3)
            self.assertEqual(list(root.iterdir()),[])
            self.assertEqual(events[-1]['completedTiles'],3)
            self.assertLessEqual(events[-1]['peakPendingBytes'],12)

    def test_byte_budget_includes_running_work_and_blocks_second_file(self):
        entered=Event();release=Event();second=Event();errors=[]
        def upload(name,path,record):
            if name=='first':entered.set();release.wait(3)
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            with UploadQueue(root,upload,workers=2,max_bytes=6) as queue:
                first=root/'one';first.write_bytes(b'1111');queue.submit('first',first,item(b'1111'))
                self.assertTrue(entered.wait(2))
                def produce():
                    try:
                        next_file=root/'two';next_file.write_bytes(b'2222')
                        queue.submit('second',next_file,item(b'2222'));second.set()
                    except BaseException as error:errors.append(error)
                thread=Thread(target=produce);thread.start()
                try:self.assertFalse(second.wait(.05),'running bytes must remain reserved')
                finally:release.set();thread.join(3)
                self.assertFalse(thread.is_alive());self.assertEqual(errors,[])
                self.assertTrue(second.is_set());self.assertLessEqual(queue.peak_bytes,6)
            self.assertEqual(list(root.iterdir()),[])

    def test_worker_exception_propagates_and_cleans_owned_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            def upload(*args):raise OSError('verification failed')
            with self.assertRaisesRegex(OSError,'verification failed'):
                with UploadQueue(root,upload,workers=1,max_bytes=10) as queue:
                    path=root/'current';path.write_bytes(b'abcd');queue.submit('tile',path,item(b'abcd'))
            self.assertEqual(list(root.iterdir()),[])

    def test_producer_interrupt_cleans_running_and_waiting_files(self):
        started=Event();release=Event();calls=[]
        def upload(name,path,record):
            started.set();release.wait(3);calls.append(name)
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            with self.assertRaises(KeyboardInterrupt):
                with UploadQueue(root,upload,workers=1,max_bytes=8) as queue:
                    for index in range(2):
                        path=root/'current';path.write_bytes(b'abcd');queue.submit(str(index),path,item(b'abcd'))
                    self.assertTrue(started.wait(2));release.set();raise KeyboardInterrupt()
            self.assertEqual(list(root.iterdir()),[])
            self.assertLessEqual(len(calls),2)

    def test_invalid_limits_and_oversize_never_schedule_upload(self):
        with tempfile.TemporaryDirectory() as folder:
            for workers,max_bytes in [(0,10),(9,10),(True,10),(1,0),(1,MAX_PENDING_BYTES+1)]:
                with self.assertRaises(ValueError):UploadQueue(folder,lambda *a:self.fail(),workers=workers,max_bytes=max_bytes)
            with UploadQueue(folder,lambda *a:self.fail(),max_bytes=3) as queue:
                path=Path(folder)/'current';path.write_bytes(b'abcd')
                with self.assertRaises(ValueError):queue.submit('tile',path,item(b'abcd'))
                self.assertTrue(path.exists(),'unscheduled file remains caller owned')
                path.unlink()

    def test_region_failure_prevents_returning_complete_validation_report(self):
        plan,download=source(archive([('tiles/2/000/000/001.gph',tile())]))
        with tempfile.TemporaryDirectory() as folder:
            tiles,headers=inventory(plan,folder,download)
            progress=[];published=[]
            def bad_upload(*args):raise OSError('full GET SHA failed')
            with self.assertRaisesRegex(OSError,'full GET SHA failed'):
                result=migrate(plan,folder,tiles,headers,bad_upload,downloader=download,progress=progress.append)
                published.append(result)
            self.assertEqual(published,[])
            self.assertFalse(any(e['event']=='migration_upload_complete' for e in progress))
            self.assertEqual(list(Path(folder).iterdir()),[])

    def test_capacity_reserves_pending_files_in_addition_to_current_tile(self):
        plan,download=source(archive([('tiles/2/000/000/001.gph',tile())]))
        reserves=[]
        with tempfile.TemporaryDirectory() as folder, patch.object(migration_stream,'require_capacity',
                side_effect=lambda work,size,reserve:reserves.append(reserve)):
            tiles,headers=inventory(plan,folder,download)
            self.assertEqual(set(reserves),{640*1024*1024});reserves.clear()
            migrate(plan,folder,tiles,headers,lambda *a:None,downloader=download)
            self.assertEqual(set(reserves),{640*1024*1024+MAX_PENDING_BYTES})

    def test_late_poison_closes_tar_before_running_worker_reads_owned_file(self):
        poison=bytearray(tile(poison=True));struct.pack_into('<Q',poison,0,18)
        plan,download=source(archive([('tiles/2/000/000/001.gph',tile()),
                                     ('tiles/2/000/000/002.gph',poison)]))
        entered=Event();tar_closed=Event();read=[]
        real_members,real_validate=migration_stream.members,migration_stream.validate_tile
        def members(*args,**kwargs):
            try:yield from real_members(*args,**kwargs)
            finally:tar_closed.set()
        def validate(path,header,headers):
            if header['graph']==18:self.assertTrue(entered.wait(3))
            return real_validate(path,header,headers)
        def upload(name,path,record):
            entered.set()
            if not tar_closed.wait(3):raise RuntimeError('tar did not close before worker join')
            read.append(path.read_bytes())
        with tempfile.TemporaryDirectory() as folder:
            tiles,headers=inventory(plan,folder,download)
            with patch.object(migration_stream,'members',members), patch.object(migration_stream,'validate_tile',validate):
                with self.assertRaisesRegex(ValueError,'edge span'):
                    migrate(plan,folder,tiles,headers,upload,downloader=download)
            self.assertEqual(read,[tile()]);self.assertEqual(list(Path(folder).iterdir()),[])

    def test_cli_worker_failure_never_publishes_manifest_or_receipt(self):
        import migrate_release_r2 as cli
        plan,download=source(archive([('tiles/2/000/000/001.gph',tile())]));plan['slug']='fixture'
        writes=[]
        class FailingPublisher:
            uploaded=reused=0
            def __init__(self,*args):pass
            def metrics(self):return {}
            def put(self,key,*args):writes.append(key);raise OSError('readback failed')
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);release=root/'release.json';release.write_text('{"tag_name":"fixture"}')
            ready=root/'ready';ready.write_bytes(b'ok\n')
            pilot=root/'pilot.json';pilot.write_text(json.dumps(dict(schema=1,manifest_published=False,
                contract='contract',bucket='fixture',verified_tiles=20)))
            argv=['migrate','--release-json',str(release),'--ready',str(ready),'--region','fixture',
                '--work',str(root/'work'),'--mode','full','--pilot-receipt',str(pilot)]
            with patch.object(sys,'argv',argv), patch.object(cli,'validate_supply'), \
                 patch.object(cli,'build_plans',return_value=[plan]), \
                 patch.object(cli,'migration_identity',return_value='contract'), \
                 patch.object(cli,'connection',return_value=(None,'fixture')), \
                 patch.object(cli,'Publisher',FailingPublisher), \
                 patch.object(cli,'inventory',side_effect=lambda p,w,**kw:inventory(p,w,download,**kw)), \
                 patch.object(cli,'migrate',side_effect=lambda *a,**kw:migrate(*a,downloader=download,**kw)), \
                 patch('builtins.print'):
                with self.assertRaisesRegex(OSError,'readback failed'):cli.main()
            self.assertEqual(len(writes),1);self.assertIn('/tiles/',writes[0])
            self.assertEqual(list((root/'work').iterdir()),[])

    def test_progress_exposes_authenticated_parts_inventory_and_only_finished_uploads(self):
        plan,download=source(archive([('tiles/2/000/000/001.gph',tile())]))
        with tempfile.TemporaryDirectory() as folder:
            progress=[]
            tiles,headers=inventory(plan,folder,download,progress=progress.append)
            migrate(plan,folder,tiles,headers,lambda *a:None,downloader=download,progress=progress.append)
            events=[e['event'] for e in progress]
            self.assertIn('inventory_complete',events);self.assertIn('source_part_verified',events)
            self.assertIn('migration_upload_complete',events)
            self.assertLess(events.index('inventory_complete'),events.index('migration_upload_started'))
            end=next(e for e in progress if e['event']=='migration_upload_complete')
            self.assertEqual((end['completedTiles'],end['pendingBytes']),(1,0))


class ConcurrentPublisher(unittest.TestCase):
    def test_counters_and_verified_reuse_remain_exact_across_workers(self):
        from botocore.exceptions import ClientError
        lock=Lock();objects={};writes=[]
        class S3:
            def get_object(self,**args):
                with lock:raw=objects.get(args['Key'])
                if raw is None:raise ClientError({'Error':{'Code':'NoSuchKey'}},'GetObject')
                return dict(Body=io.BytesIO(raw))
            def put_object(self,**args):
                raw=args['Body'].read()
                with lock:objects[args['Key']]=raw;writes.append(args['Key'])
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'fixture';raw=b'validated tile';path.write_bytes(raw)
            publisher=Publisher(S3(),'fixture')
            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(lambda n:publisher.put(str(n),path,item(raw)),range(64)))
                list(pool.map(lambda n:publisher.put(str(n),path,item(raw)),range(64)))
            self.assertEqual((publisher.uploaded,publisher.reused),(64,64))
            stats=publisher.metrics();self.assertEqual(len(writes),64)
            self.assertEqual((stats['putCalls'],stats['verifyCalls']),(64,192))
            self.assertEqual(stats['verifiedBytes'],128*len(raw))
            self.assertEqual(stats['uploadedBytes'],64*len(raw))


if __name__=='__main__':unittest.main()
