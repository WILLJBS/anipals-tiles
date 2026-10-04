"""Synthetic unit checks only; no real source, native engine, R2 or activation evidence."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'tools'), str(ROOT/'deploy')]
from native_r2_acceptance import Counter
from native_runtime_acceptance import isolated_volume, run_restart, checked_bucket
from native_runtime_session import exercise, check_restart
from test_regional_ownership import fixture, load
from test_native_scope_geometry import encode
from regional_gc import activate
from regional_ownership import read_control, read_pointer
from regional_engine import EngineError


class FakeEngine:
    def __init__(self, counter, bad_remote=False, bad_local_route=False):
        self.counter, self.remote_routes = counter, 0
        self.bad_remote, self.bad_local_route = bad_remote, bad_local_route

    def request(self, region, action, payload, **_):
        remote = region.get('storage') == 'r2'
        if action == 'status':
            return dict(version='3.3.0')
        if action == 'locate':
            if remote and self.bad_remote:
                raise EngineError('synthetic failed activation probe')
            return [dict(edges=[dict(correlated_lat=.5,correlated_lon=.5)])]
        if remote:
            self.remote_routes += 1
            if self.remote_routes == 1:
                self.counter.http += 1; self.counter.fetches += 1; self.counter.bytes += 3
        coords = [(.5,.5),(.5,.502)]
        if not remote and self.bad_local_route:
            coords = [(.5,.5),(.501,.502)]
        return dict(trip=dict(units='kilometers',summary=dict(length=.223),
                              legs=[dict(shape=encode(coords))]))


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.base,self.index,self.manifest = fixture()
        self.composite = load(self.base,self.index,self.manifest)
        self.parent = self.root/'regions/local'
        tiles = self.parent/('a'*64)/'tiles'; tiles.mkdir(parents=True)
        local = dict(slug='local',fingerprint='a'*64,release='synthetic-local',
                     tile_dir=str(tiles.relative_to(self.root)),tile_count=1)
        (tiles.parent/'.complete.json').write_text(json.dumps(local)); activate(self.root,local)
        self.counter = Counter(); self.engine = FakeEngine(self.counter)
        self.payload = dict(costing='pedestrian',locations=[dict(lat=.5,lon=.5),dict(lat=.5,lon=.502)])
        self.probes = [dict(slug='synthetic',lat=.5,lng=.5)]
        self.bridge = SimpleNamespace(failures=0)

    def run_session(self):
        return exercise(self.composite,self.root,self.engine,self.payload,self.probes,self.counter,self.bridge)

    def test_session_switches_routes_rejects_stale_workers_and_retains_rollback(self):
        result = self.run_session()
        self.assertFalse(result['restart_verified']); self.assertFalse(result['production_activated'])
        self.assertEqual(read_control(self.parent)['mode'],'rollback')
        self.assertEqual(read_pointer(self.parent)['fingerprint'],'a'*64)
        restarted = check_restart(self.composite,self.root,FakeEngine(Counter()),self.payload,self.probes,result)
        self.assertTrue(restarted['restart_verified'])
        self.assertFalse((self.parent/result['remote_fingerprint']).exists())
        self.assertTrue((self.parent/result['local_fingerprint']).exists())

    def test_failed_native_activation_never_selects_remote(self):
        self.engine.bad_remote = True
        with self.assertRaises(EngineError): self.run_session()
        self.assertEqual(read_pointer(self.parent)['fingerprint'],'a'*64)
        self.assertEqual(read_control(self.parent)['mode'],'selected')
        from regional_catalog import Catalog
        self.assertEqual(Catalog(self.root,self.base).available(),{})

    def test_different_local_route_is_not_accepted_as_successful_rollback(self):
        self.engine.bad_local_route = True
        with self.assertRaisesRegex(ValueError,'SHAPE'): self.run_session()

    def test_restart_rejects_lost_mode_missing_retention_and_changed_route(self):
        result = self.run_session(); path = self.parent/'ownership.json'; original = path.read_bytes()
        state = json.loads(original); state['mode'] = 'selected'; path.write_text(json.dumps(state))
        with self.assertRaisesRegex(ValueError,'DURABLE_ROLLBACK'):
            check_restart(self.composite,self.root,self.engine,self.payload,self.probes,result)
        path.write_bytes(original)
        with self.assertRaisesRegex(ValueError,'SHAPE'):
            check_restart(self.composite,self.root,FakeEngine(Counter(),bad_local_route=True),self.payload,self.probes,result)
        marker = self.parent/('a'*64)/'.complete.json'; marker.unlink()
        with self.assertRaises(FileNotFoundError):
            check_restart(self.composite,self.root,self.engine,self.payload,self.probes,result)

    def test_second_session_cannot_reuse_already_mutated_volume(self):
        self.run_session()
        with self.assertRaisesRegex(ValueError,'FRESH_ISOLATED'): self.run_session()

    def test_volume_guard_rejects_outside_root_symlink_and_untrusted_label(self):
        parent = self.root/'runner'; parent.mkdir(); volume = parent/'isolated'; volume.mkdir()
        self.assertEqual(isolated_volume(volume,str(parent)),volume)
        for candidate,temp in [(self.root,str(parent)),(parent,str(parent)),(volume,None)]:
            with self.subTest(candidate=candidate,temp=temp),self.assertRaises(ValueError):
                isolated_volume(candidate,temp)
        alias = parent/'alias'; alias.symlink_to(volume,target_is_directory=True)
        with self.assertRaises(ValueError): isolated_volume(alias,str(parent))

    def test_reader_bucket_must_match_verified_catalog_before_any_get(self):
        objects = SimpleNamespace(read=lambda *_: b'{"bucket":"synthetic-bucket"}')
        request = b'{"catalog":{}}'
        checked_bucket('synthetic-bucket',request,objects)
        for actual in (None,'another-bucket'):
            with self.assertRaisesRegex(ValueError,'BUCKET_DIFFERS'):
                checked_bucket(actual,request,objects)

    def test_restart_spawns_new_interpreter_without_credentials_and_binds_result(self):
        expected = dict(index_sha256='d'*64,local_fingerprint='a'*64)
        state = dict(expected=expected)
        def child(command, **options):
            self.assertEqual(command[0],sys.executable)
            self.assertEqual(options['timeout'],90)
            self.assertNotIn('R2_SECRET_ACCESS_KEY',options['env'])
            raw = Path(command[command.index('--restart-state')+1]).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(),command[command.index('--restart-sha')+1])
            Path(command[command.index('--output')+1]).write_text(json.dumps(
                dict(expected,restart_verified=True,production_activated=False)))
        with patch.dict(os.environ,{'R2_SECRET_ACCESS_KEY':'synthetic-only'}),patch(
                'native_runtime_acceptance.subprocess.run',side_effect=child) as process:
            self.assertTrue(run_restart(state,self.root,'b'*40)['restart_verified'])
            process.assert_called_once()

    def test_restart_failure_or_substitution_never_marks_success(self):
        state = dict(expected=dict(index_sha256='d'*64,local_fingerprint='a'*64))
        with patch('native_runtime_acceptance.subprocess.run',side_effect=subprocess.TimeoutExpired('synthetic',90)):
            with self.assertRaises(subprocess.TimeoutExpired):run_restart(state,self.root,'b'*40)
        (self.root/'restart-input.json').unlink()
        def wrong(command,**_):
            Path(command[command.index('--output')+1]).write_text('{}')
        with patch('native_runtime_acceptance.subprocess.run',side_effect=wrong):
            with self.assertRaisesRegex(ValueError,'RESULT_DIFFERS'):run_restart(state,self.root,'b'*40)


if __name__ == '__main__':
    unittest.main()
