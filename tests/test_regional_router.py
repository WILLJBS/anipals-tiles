"""Independent geometry, graph activation and actual child-process lifecycle checks."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'deploy'))
sys.path.insert(0, str(ROOT/'tools'))
from regional_catalog import Catalog, contains, locations
from regional_engine import Engine, EngineError
from regional_router import Router


def box(west=0, south=0, east=10, north=10):
    return [[west,south],[east,south],[east,north],[west,north],[west,south]]


def coverage():
    return dict(features=[dict(properties=dict(slug='a'), geometry=dict(type='Polygon',coordinates=[box()])),
                          dict(properties=dict(slug='b'), geometry=dict(type='Polygon',coordinates=[box(8,0,18,10)]))])


def install(root, slug, fingerprint, active=False):
    folder=Path(root)/'regions'/slug/fingerprint
    (folder/'tiles').mkdir(parents=True)
    value=dict(slug=slug,fingerprint=fingerprint,tile_dir=str((folder/'tiles').relative_to(root)),release='test',tile_count=1)
    (folder/'.complete.json').write_text(json.dumps(value))
    if active:
        (folder.parent/'active.json').write_text(json.dumps(dict(fingerprint=fingerprint,release='test')))
    return value


class GeometryTests(unittest.TestCase):
    def test_boundary_hole_and_disconnected_dateline_pieces(self):
        geometry=dict(type='Polygon',coordinates=[box(),box(2,2,4,4)])
        for point in [(0,0),(10,5),(1,1)]: self.assertTrue(contains(geometry,point))
        for point in [(3,3),(-1,5),(5,11)]: self.assertFalse(contains(geometry,point))
        pacific=dict(type='MultiPolygon',coordinates=[[box(170,50,180,70)],[box(-180,50,-170,70)]])
        for point in [(179,60),(-179,60),(180,60),(-180,60)]: self.assertTrue(contains(pacific,point))
        self.assertFalse(contains(pacific,(0,60)))

    def test_both_endpoints_must_share_one_extract(self):
        with tempfile.TemporaryDirectory() as root:
            catalog=Catalog(root,coverage())
            self.assertEqual(catalog.candidates([(9,5),(9,6)]),['a','b'])
            self.assertEqual(catalog.candidates([(1,5),(17,5)]),[])
            self.assertEqual(catalog.candidates([(10,5),(10,6)]),['a','b'])

    def test_reject_malformed_coordinates_and_nonpedestrian_costing(self):
        payload=dict(locations=[dict(lat=1,lon=2),dict(lat=3,lon=4)],costing='pedestrian')
        self.assertEqual(locations(payload),[(2,1),(4,3)])
        for value in [True,float('nan'),float('inf'),91,'3']:
            with self.assertRaises(ValueError): locations(dict(payload,locations=[dict(lat=value,lon=2),dict(lat=3,lon=4)]))
        with self.assertRaises(ValueError): locations(dict(payload,costing='auto'))


class CatalogTests(unittest.TestCase):
    def test_unverified_candidate_never_activates_or_displaces_current_graph(self):
        with tempfile.TemporaryDirectory() as root:
            catalog=Catalog(root,coverage())
            install(root,'a','a'*64)
            self.assertEqual(catalog.available(),{})
            install(root,'a','b'*64,active=True)
            available=catalog.available()
            self.assertEqual(available['a']['fingerprint'],'b'*64)
            self.assertEqual(Path(available['a']['tile_dir']),(Path(root)/'regions/a'/('b'*64)/'tiles').resolve())

    def test_marker_cannot_point_at_another_region_or_symlink_outside_root(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as outside:
            row=install(root,'a','a'*64,active=True)
            folder=Path(root)/'regions/a'/('a'*64)
            row['tile_dir']=outside
            (folder/'.complete.json').write_text(json.dumps(row))
            with self.assertRaises(ValueError): Catalog(root,coverage()).available()
            row['tile_dir']=str((folder/'tiles').relative_to(root))
            (folder/'.complete.json').write_text(json.dumps(row))
            (folder/'tiles').rmdir(); (folder/'tiles').symlink_to(outside,target_is_directory=True)
            with self.assertRaises(ValueError): Catalog(root,coverage()).available()


class NativeProcessTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name).resolve()
        native=self.root/'valhalla_service'
        native.write_text('#!/bin/sh\ncase "$2" in\ncrash) kill -SEGV $$;;\nhang) exec sleep 30;;\nslow) sleep 1; echo \'{"ok":true}\';;\n*) echo \'{"ok":true}\';;\nesac\n')
        native.chmod(0o755)
        self.environment=patch.dict(os.environ,PATH=str(self.root)+os.pathsep+os.environ['PATH']);self.environment.start()
        self.launch_patch=None
        if sys.platform == 'darwin':
            # macOS refuses lowering RLIMIT_AS; Linux CI exercises the real limiter.
            # Keep the actual subprocess/pipe/process-group lifecycle on both systems.
            launch=subprocess.Popen
            self.launch_patch=patch('regional_engine.subprocess.Popen',side_effect=lambda command,**kwargs:launch([str(native)]+command[3:],**kwargs))
            self.launch_patch.start()
        self.region=dict(slug='a',fingerprint='a'*64,tile_dir=str(self.root/'graph-a'))
        (self.root/'graph-a').mkdir()
        (self.root/'.complete.json').write_text(json.dumps(self.region))
        self.engine=Engine(dict(mjolnir={},loki={},thor={}),self.root/'config',concurrency=1,timeout=.3)

    def tearDown(self):
        self.engine.close()
        if self.launch_patch:self.launch_patch.stop()
        self.environment.stop();self.temp.cleanup()

    def test_native_crash_and_deadline_release_slot_for_next_request(self):
        for action in ['crash','hang']:
            with self.assertRaises(EngineError): self.engine.request(self.region,action,{})
            self.assertEqual(self.engine.children,set())
            self.assertEqual(self.engine.request(self.region,'route',{}),dict(ok=True))

    def test_capacity_is_bounded_while_child_is_running(self):
        self.engine.timeout=2
        failures=[]
        def run():
            try:self.engine.request(self.region,'slow',{})
            except Exception as error:failures.append(error)
        thread=threading.Thread(target=run);thread.start()
        deadline=time.monotonic()+2
        while not self.engine.children and time.monotonic()<deadline:time.sleep(.005)
        try:
            with self.assertRaisesRegex(EngineError,'capacity'): self.engine.request(self.region,'route',{})
        finally:thread.join(3)
        self.assertFalse(thread.is_alive());self.assertEqual(failures,[])
        self.assertEqual(self.engine.request(self.region,'route',{}),dict(ok=True))

    def test_close_reaps_native_children_and_rejects_new_work(self):
        self.engine.timeout=3
        failures=[]
        def run():
            try:self.engine.request(self.region,'hang',{})
            except EngineError as error:failures.append(error)
        thread=threading.Thread(target=run);thread.start()
        deadline=time.monotonic()+2
        while not self.engine.children and time.monotonic()<deadline:time.sleep(.005)
        try:
            self.engine.close();thread.join(2)
            self.assertFalse(thread.is_alive(),'shutdown must not leave a native request alive')
            self.assertEqual(self.engine.children,set())
            with self.assertRaises(EngineError):self.engine.request(self.region,'route',{})
        finally:
            for child in list(self.engine.children):
                try:os.killpg(child.pid,signal.SIGKILL)
                except ProcessLookupError:pass
            thread.join(4)


class RouterTests(unittest.TestCase):
    def test_empty_city_probe_uses_real_graph_node_but_native_crashes_do_not(self):
        with tempfile.TemporaryDirectory() as root:
            install(root,'a','a'*64)
            calls=[]
            class Native:
                def request(self,region,action,payload,**kwargs):
                    calls.append(payload['locations'][0])
                    return [{'edges':[] if len(calls)==1 else [{'id':1}]}]
            router=Router(Catalog(root,coverage()),Native(),[dict(slug='city',lat=5,lng=5)])
            with patch('regional_router.graph_points',return_value=iter([dict(lat=1,lng=1)])):
                self.assertTrue(router.verify('a','a'*64)['verified'])
            self.assertEqual(calls,[dict(lat=5,lon=5),dict(lat=1,lon=1)])
            class Broken:
                def request(self,*args):raise EngineError('native crashed',503)
            router=Router(Catalog(root,coverage()),Broken(),[dict(slug='city',lat=5,lng=5)])
            consumed=[]
            def fallback(*args,**kwargs):
                consumed.append(True)
                yield dict(lat=1,lng=1)
            with patch('regional_router.graph_points',side_effect=fallback):
                with self.assertRaises(EngineError):router.verify('a','a'*64)
                self.assertEqual(consumed,[])

    def test_canada_requires_both_registered_historical_probes(self):
        with tempfile.TemporaryDirectory() as root:
            install(root,'north-america-canada','c'*64)
            shape=coverage();shape['features'][0]['properties']['slug']='north-america-canada'
            class Native:
                def request(self,*args):return [{'edges':[{'id':1}]}]
            for probes in [[],[dict(slug='toronto',lat=5,lng=5)]]:
                router=Router(Catalog(root,shape),Native(),probes)
                with self.assertRaises(EngineError):router.verify('north-america-canada','c'*64)
            probes=[dict(slug=city,lat=5,lng=5) for city in ['toronto','montreal']]
            router=Router(Catalog(root,shape),Native(),probes)
            self.assertTrue(router.verify('north-america-canada','c'*64)['verified'])

    def test_overlap_retries_candidate_without_ever_combining_graph_roots(self):
        with tempfile.TemporaryDirectory() as root:
            install(root,'a','a'*64,active=True);install(root,'b','b'*64,active=True)
            calls=[]
            class Native:
                def request(self,region,action,payload,**kwargs):
                    calls.append((region['slug'],region['tile_dir']))
                    if region['slug']=='a':raise EngineError('no route',404)
                    return dict(trip=dict(legs=[{}],summary=dict(length=.2)))
            router=Router(Catalog(root,coverage()),Native(),[])
            result,region=router.route(dict(locations=[dict(lat=5,lon=9),dict(lat=6,lon=9)],costing='pedestrian'))
            self.assertEqual(region['slug'],'b');self.assertEqual([s for s,_ in calls],['a','b'])
            self.assertNotEqual(calls[0][1],calls[1][1]);self.assertTrue(router.status()['ready'])
            with self.assertRaisesRegex(EngineError,'single extract'):
                router.route(dict(locations=[dict(lat=5,lon=1),dict(lat=5,lon=17)],costing='pedestrian'))


if __name__=='__main__': unittest.main()
