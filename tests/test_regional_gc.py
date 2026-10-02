import fcntl
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'deploy'))
import regional_download as download
import regional_gc as gc
from regional_engine import Engine, EngineError

SLUG = 'north-america-canada'


class RegionalGCTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.parent = self.root / 'regions' / SLUG
        self.old = self.graph('a')
        self.new = self.graph('b')
        self.candidate = self.graph('c')
        self.activate(self.old)

    def tearDown(self):
        self.temp.cleanup()

    def graph(self, letter):
        fingerprint = letter * 64
        graph = self.parent / fingerprint
        tiles = graph / 'tiles'; tiles.mkdir(parents=True)
        (tiles / 'data.gph').write_bytes(letter.encode() * 100)
        descriptor = dict(slug=SLUG, fingerprint=fingerprint, release='release-' + letter,
                          tile_count=1, tile_dir=str(tiles.relative_to(self.root)))
        (graph / '.complete.json').write_text(json.dumps(descriptor))
        return descriptor

    def activate(self, descriptor):
        download.activate_region(self.root, descriptor, dict(
            slug=SLUG, fingerprint=descriptor['fingerprint'], verified=True))

    def path(self, descriptor):
        return self.parent / descriptor['fingerprint']

    def queue(self):
        return json.loads((self.parent / 'retired.json').read_text())['fingerprints']

    def test_switch_retires_only_former_active_and_preserves_unverified_candidate(self):
        with self.assertRaises(ValueError):
            download.activate_region(self.root, self.candidate, {'verified': False})
        self.assertFalse((self.parent / 'retired.json').exists())
        self.activate(self.new)
        self.assertEqual(self.queue(), [self.old['fingerprint']])
        self.assertEqual(gc.collect_retired(self.root), 0)
        self.assertFalse(self.path(self.old).exists())
        self.assertTrue(self.path(self.new).exists())
        self.assertTrue(self.path(self.candidate).exists())
        self.assertEqual(self.queue(), [])

    def test_real_process_shared_lease_defers_nonblocking_gc(self):
        lease = self.path(self.old) / '.lease.lock'
        script = 'import fcntl,sys,time; f=open(sys.argv[1],"a"); fcntl.flock(f,fcntl.LOCK_SH); print("ready",flush=True); time.sleep(600)'
        process = subprocess.Popen([sys.executable, '-c', script, str(lease)], stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(), 'ready')
            self.activate(self.new)
            started = time.monotonic()
            self.assertEqual(gc.collect_retired(self.root), 1)
            self.assertLess(time.monotonic() - started, 1)
            self.assertTrue(self.path(self.old).exists())
        finally:
            process.kill(); process.wait(); process.stdout.close()
        self.assertEqual(gc.collect_retired(self.root), 0)
        self.assertFalse(self.path(self.old).exists())

    def test_killed_parent_does_not_release_inherited_native_child_lease(self):
        lease = self.path(self.old) / '.lease.lock'
        script = '\n'.join([
            'import fcntl,os,subprocess,sys,time',
            'fd=os.open(sys.argv[1],os.O_CREAT|os.O_RDWR,0o600)',
            'fcntl.flock(fd,fcntl.LOCK_SH)',
            'child=subprocess.Popen([sys.executable,"-c","import time; time.sleep(600)"],pass_fds=(fd,),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)',
            'print(child.pid,flush=True)',
            'time.sleep(600)',
        ])
        process = subprocess.Popen([sys.executable, '-c', script, str(lease)], stdout=subprocess.PIPE, text=True)
        child_pid = None
        try:
            child_pid = int(process.stdout.readline().strip())
            self.activate(self.new)
            process.kill(); process.wait()
            os.kill(child_pid, 0)
            self.assertEqual(gc.collect_retired(self.root), 1)
            self.assertTrue(self.path(self.old).exists())
            os.kill(child_pid, signal.SIGKILL)
            deadline = time.monotonic() + 3
            while gc.collect_retired(self.root) and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertEqual(gc.collect_retired(self.root), 0)
            self.assertFalse(self.path(self.old).exists())
        finally:
            if process.poll() is None: process.kill()
            process.wait(); process.stdout.close()
            if child_pid:
                try: os.kill(child_pid, signal.SIGKILL)
                except ProcessLookupError: pass

    def test_rename_crash_recovers_durable_tombstone_and_queue(self):
        self.activate(self.new)
        with patch.object(gc.shutil, 'rmtree', side_effect=OSError('power loss')), self.assertRaises(OSError):
            gc.collect_retired(self.root)
        trash = self.parent / ('.retired-' + self.old['fingerprint'])
        self.assertTrue(trash.is_dir())
        self.assertFalse(self.path(self.old).exists())
        self.assertEqual(self.queue(), [self.old['fingerprint']])
        self.assertEqual(gc.collect_retired(self.root), 0)
        self.assertFalse(trash.exists())
        self.assertTrue(self.path(self.new).exists())

    def test_activation_crash_before_pointer_switch_cannot_delete_current(self):
        original = gc.atomic_json
        def fail_active(path, value):
            if path.name == 'active.json': raise OSError('power loss')
            original(path, value)
        with patch.object(gc, 'atomic_json', side_effect=fail_active), self.assertRaises(OSError):
            self.activate(self.new)
        self.assertEqual(self.queue(), [self.old['fingerprint']])
        self.assertEqual(gc.collect_retired(self.root), 0)
        self.assertTrue(self.path(self.old).exists())
        self.assertTrue(self.path(self.new).exists())
        self.assertEqual(self.queue(), [])
        self.activate(self.new)
        self.assertEqual(gc.collect_retired(self.root), 0)
        self.assertFalse(self.path(self.old).exists())

    def test_deleted_graph_before_queue_commit_recovers(self):
        self.activate(self.new)
        original = gc.atomic_json
        def fail_queue(path, value):
            if path.name == 'retired.json': raise OSError('power loss')
            original(path, value)
        with patch.object(gc, 'atomic_json', side_effect=fail_queue), self.assertRaises(OSError):
            gc.collect_retired(self.root)
        self.assertFalse(self.path(self.old).exists())
        self.assertEqual(self.queue(), [self.old['fingerprint']])
        self.assertEqual(gc.collect_retired(self.root), 0)
        self.assertEqual(self.queue(), [])

    def test_symlink_and_traversal_are_rejected_without_external_deletion(self):
        self.activate(self.new)
        outside = self.root / 'outside'; outside.mkdir(); (outside / 'keep').write_text('safe')
        lease = self.path(self.old) / '.lease.lock'; lease.symlink_to(outside / 'lock')
        with self.assertRaises(ValueError): gc.collect_retired(self.root)
        self.assertTrue((outside / 'keep').exists())
        lease.unlink()
        (self.parent / 'retired.json').write_text(json.dumps({'fingerprints': ['../../outside']}))
        with self.assertRaises(ValueError): gc.collect_retired(self.root)
        self.assertTrue((outside / 'keep').exists())
        evil = dict(self.new, slug='../outside')
        with self.assertRaises(ValueError): gc.activate(self.root, evil)

    def test_graph_and_tombstone_symlinks_never_followed(self):
        self.activate(self.new)
        external = self.root / 'external'; external.mkdir()
        graph = self.path(self.old)
        moved = self.root / 'kept-old'; graph.rename(moved); graph.symlink_to(external)
        with self.assertRaises(ValueError): gc.collect_retired(self.root)
        self.assertTrue(external.is_dir())
        graph.unlink(); moved.rename(graph)
        trash = self.parent / ('.retired-' + self.old['fingerprint']); trash.symlink_to(external)
        with self.assertRaises(ValueError): gc.collect_retired(self.root)
        self.assertTrue(graph.is_dir())
        self.assertTrue(external.is_dir())

    def test_engine_request_lease_blocks_gc_until_actual_child_finishes(self):
        engine = Engine(dict(mjolnir={}, loki={}, thor={}), self.root / 'runtime', timeout=3)
        region = dict(self.old, tile_dir=str(self.root / self.old['tile_dir']))
        ready, release = self.root / 'ready', self.root / 'release'
        real_launch = subprocess.Popen
        script = '\n'.join([
            'import json,os,pathlib,sys,time',
            'os.fstat(int(sys.argv[3]))',  # proves FD survives the real exec
            'pathlib.Path(sys.argv[1]).write_text("ready")',
            'while not pathlib.Path(sys.argv[2]).exists(): time.sleep(.005)',
            'print(json.dumps({"ok":True}))',
        ])
        def launch(command, **kwargs):
            return real_launch([sys.executable, '-c', script, str(ready), str(release),
                                str(kwargs['pass_fds'][0])], **kwargs)
        results, errors = [], []
        def request():
            try: results.append(engine.request(region, 'route', {}))
            except Exception as error: errors.append(error)
        thread = threading.Thread(target=request)
        try:
            with patch('regional_engine.subprocess.Popen', side_effect=launch):
                thread.start()
                deadline = time.monotonic() + 2
                while not ready.exists() and time.monotonic() < deadline: time.sleep(.005)
                self.assertTrue(ready.exists())
                self.activate(self.new)
                self.assertEqual(gc.collect_retired(self.root), 1)
                self.assertTrue(self.path(self.old).exists())
                release.write_text('finish'); thread.join(3)
                self.assertFalse(thread.is_alive())
                self.assertEqual(errors, [])
                self.assertEqual(results, [{'ok': True}])
                self.assertEqual(gc.collect_retired(self.root), 0)
                self.assertFalse(self.path(self.old).exists())
        finally:
            release.write_text('finish'); engine.close(); thread.join(3)

    def test_engine_exclusive_gc_lock_is_nonblocking_and_symlink_safe(self):
        engine = Engine(dict(mjolnir={}, loki={}, thor={}), self.root / 'runtime', timeout=3)
        region = dict(self.old, tile_dir=str(self.root / self.old['tile_dir']))
        lease = self.path(self.old) / '.lease.lock'
        try:
            with gc.file_lock(lease, fcntl.LOCK_EX):
                started = time.monotonic()
                with self.assertRaisesRegex(EngineError, 'lease unavailable'):
                    engine.request(region, 'route', {})
                self.assertLess(time.monotonic() - started, .5)
            lease.unlink(); lease.symlink_to(self.root / 'external-lock')
            with self.assertRaisesRegex(EngineError, 'lease unavailable'):
                engine.request(region, 'route', {})
            self.assertFalse((self.root / 'external-lock').exists())
            self.assertEqual(engine.children, set())
        finally:
            engine.close()

    def test_download_pass_stays_pending_until_retired_lease_drains(self):
        plan = dict(slug=SLUG, fingerprint=self.new['fingerprint'], parts=[])
        health = lambda descriptor: dict(slug=SLUG, fingerprint=descriptor['fingerprint'], verified=True)
        with patch.object(download, 'prepare_region', return_value=self.new):
            with gc.file_lock(self.path(self.old) / '.lease.lock', fcntl.LOCK_SH):
                self.assertEqual(download.run_pass(self.root, [plan], health), ['retired-graphs-awaiting-leases'])
                self.assertTrue(self.path(self.old).exists())
            self.assertEqual(download.run_pass(self.root, [plan], health), [])
        self.assertFalse(self.path(self.old).exists())
        self.assertTrue(self.path(self.new).exists())


if __name__ == '__main__': unittest.main()
