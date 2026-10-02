import hashlib
import http.server
import importlib.util
import json
import struct
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def module(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result

PLAN = module('plan', 'deploy/release-plan.py')
MANIFEST = module('manifest', 'tools/release_manifest.py')
TILES = module('tiles', 'tools/validate_tiles.py')
BODY = b'0123456789' * 100


class DownloadTests(unittest.TestCase):
    def test_range_complete_partial_ignored_oversized_and_bad_hash(self):
        requests = []
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append((self.path, self.headers.get('Range')))
                offset = int(self.headers.get('Range', 'bytes=0-')[6:-1])
                if self.path == '/ignore':
                    offset = 0
                if offset >= len(BODY):
                    self.send_response(416); self.end_headers(); return
                self.send_response(206 if offset else 200)
                self.send_header('Content-Length', str(len(BODY)-offset))
                if offset:
                    self.send_header('Content-Range', 'bytes %d-%d/%d' % (offset, len(BODY)-1, len(BODY)))
                self.end_headers(); self.wfile.write(BODY[offset:])
            def log_message(self, *_):
                pass
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            with tempfile.TemporaryDirectory() as temp:
                file = Path(temp)/'part'
                digest = hashlib.sha256(BODY).hexdigest()
                def run(path='/range', expected=digest):
                    return subprocess.run(['sh', str(ROOT/'deploy/download-part.sh'), str(file),
                        'http://127.0.0.1:%d%s' % (server.server_port,path),str(len(BODY)),expected],
                        capture_output=True,timeout=10)
                file.write_bytes(BODY)
                self.assertEqual(run().returncode,0); self.assertEqual(requests,[])
                file.write_bytes(BODY[:123]); self.assertEqual(run().returncode,0)
                self.assertEqual(requests[-1][1],'bytes=123-'); self.assertEqual(file.read_bytes(),BODY)
                file.write_bytes(BODY[:123]); before=len(requests)
                self.assertEqual(run('/ignore').returncode,0)
                self.assertEqual(len(requests)-before,2); self.assertEqual(file.read_bytes(),BODY)
                file.write_bytes(BODY+b'oversize'); self.assertEqual(run().returncode,0)
                file.write_bytes(b'x'*len(BODY)); self.assertEqual(run().returncode,0)
                file.unlink(); self.assertNotEqual(run(expected='0'*64).returncode,0)
                self.assertFalse(file.exists(),'bad download must not become permanent poison')
        finally:
            server.shutdown(); server.server_close(); thread.join()


class ReleaseTests(unittest.TestCase):
    def fixture(self):
        part=dict(name='tiles-a.tar-00',size=10,sha256='a'*64)
        validation=dict(validator='gph-v3-index-v1',tiles=1,tile_hashes={'2/000/000/001.gph':'b'*64})
        manifest=dict(slug='a',image='pinned',validation=validation,parts=[part])
        release=dict(draft=False,prerelease=False,assets=[dict(name='READY'),
            dict(name=part['name'],size=10,digest='sha256:'+'a'*64,browser_download_url='https://example.com/p')])
        roster={'region':[{'slug':'a','region':'a'}]}
        coverage = self.coverage(roster)
        manifest.update(coverage_sha256=MANIFEST.COVERAGE.digest(coverage['features'][0]),
                        pbf_url=coverage['features'][0]['properties']['pbf_url'])
        return release,roster,manifest

    def coverage(self, roster):
        features = [dict(type='Feature', properties=dict(id=r['region'], urls=dict(pbf='https://download.geofabrik.de/'+r['region']+'-latest.osm.pbf')),
                         geometry=dict(type='Polygon', coordinates=[[[0,0],[1,0],[1,1],[0,0]]])) for r in roster['region']]
        return MANIFEST.COVERAGE.build(dict(type='FeatureCollection', features=features), roster, 'f'*64)

    def test_exact_roster_gaps_digest_image_and_collision(self):
        release,roster,m=self.fixture()
        self.assertEqual(len(PLAN.plan(release,roster)),1)
        self.assertTrue(MANIFEST.ready(release,roster,[m],'pinned',self.coverage(roster))['production'])
        for change in ['subset','gap','digest','prerelease']:
            r=json.loads(json.dumps(release)); roster2=json.loads(json.dumps(roster))
            if change=='subset': roster2['region'].append({'slug':'b'})
            if change=='gap': r['assets'][1]['name']='tiles-a.tar-01'
            if change=='digest': del r['assets'][1]['digest']
            if change=='prerelease': r['prerelease']=True
            with self.assertRaises(ValueError): PLAN.plan(r,roster2)
        with self.assertRaises(ValueError): MANIFEST.ready(release,roster,[m],'other-image',self.coverage(roster))
        other=json.loads(json.dumps(m)); other['slug']='b'; other['parts'][0]['name']='tiles-b.tar-00'
        other['validation']['tile_hashes']['2/000/000/001.gph']='c'*64
        release['assets'].append(dict(name='tiles-b.tar-00',size=10,digest='sha256:'+'a'*64))
        roster['region'].append({'slug':'b','region':'b'})
        coverage = self.coverage(roster)
        other.update(coverage_sha256=MANIFEST.COVERAGE.digest(coverage['features'][1]),
                     pbf_url=coverage['features'][1]['properties']['pbf_url'])
        result = MANIFEST.ready(release,roster,[m,other],'pinned',coverage)
        self.assertEqual(result['graph_layout'],'isolated-regions-v1')
        self.assertNotEqual(result['region_manifests']['a']['graph_fingerprint'],
                            result['region_manifests']['b']['graph_fingerprint'])

    def test_existing_release_full_roster_remains_readable(self):
        path=Path('/tmp/navwork/release.json')
        if not path.exists(): self.skipTest('local historical release evidence unavailable')
        rows=PLAN.plan(json.loads(path.read_text()),json.loads((ROOT/'deploy/regions.json').read_text()))
        self.assertEqual(len(set(row[0] for row in rows)),61)


class TileTests(unittest.TestCase):
    def tile(self):
        # One road node, one edge and one spatial-bin reference; exact V3 ABI.
        data=bytearray(376)
        struct.pack_into('<Q',data,0,10)  # tile 1, level 2
        data[16:22]=b'3.3.0\0'
        struct.pack_into('<Q',data,40,1|(1<<21))
        struct.pack_into('<4I',data,96,360,360,360,376)
        struct.pack_into('<25I',data,116,*([1]*25))
        struct.pack_into('<III',data,216,376,0,376)
        struct.pack_into('<Q',data,280,1<<21)  # node starts edge 0, count 1
        struct.pack_into('<Q',data,304,10)     # edge endnode tile 1 node 0
        struct.pack_into('<Q',data,352,10)     # spatial bin edge 0
        return data

    def test_all_node_and_bin_indexes_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'2/000/000/001.gph'; path.parent.mkdir(parents=True)
            data=self.tile(); path.write_bytes(data)
            self.assertEqual(TILES.validate(temp)['tiles'],1)
            struct.pack_into('<Q',data,280,5|(1<<21)); path.write_bytes(data)
            with self.assertRaisesRegex(ValueError,'edge span'): TILES.validate(temp)
            data=self.tile(); struct.pack_into('<Q',data,352,10|(2<<25)); path.write_bytes(data)
            with self.assertRaisesRegex(ValueError,'edges index'): TILES.validate(temp)
            data=self.tile()[:-1]; path.write_bytes(data)
            with self.assertRaisesRegex(ValueError,'end_offset'): TILES.validate(temp)
            data=self.tile(); data[16:22]=b'3.4.0\0'; path.write_bytes(data)
            with self.assertRaisesRegex(ValueError,'unsupported tile ABI'): TILES.validate(temp)

    def test_install_collision_rejects_before_mutating_existing_graph(self):
        import tarfile
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); target=root/'live'; target.mkdir(); (target/'a.gph').write_bytes(b'old')
            supply=root/'tiles'; supply.mkdir(); (supply/'a.gph').write_bytes(b'new'); (supply/'z.gph').write_bytes(b'other')
            archive=root/'tiles.tar'
            with tarfile.open(archive,'w') as tar: tar.add(supply,arcname='tiles')
            stage=root/'stage'; stage.mkdir()
            run=subprocess.run(['python3',str(ROOT/'deploy/install-tiles.py'),str(archive),str(stage),str(target)],capture_output=True)
            self.assertNotEqual(run.returncode,0)
            self.assertEqual((target/'a.gph').read_bytes(),b'old'); self.assertFalse((target/'z.gph').exists())

    def test_installer_waits_for_exclusive_commit_lock(self):
        import fcntl
        import tarfile
        import time
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); target=root/'live'; target.mkdir()
            supply=root/'tiles'; supply.mkdir(); (supply/'a.gph').write_bytes(b'first')
            archive=root/'tiles.tar'
            with tarfile.open(archive,'w') as tar: tar.add(supply,arcname='tiles')
            stage=root/'stage'; stage.mkdir()
            with (target/'.install.lock').open('a') as lock:
                fcntl.flock(lock,fcntl.LOCK_EX)
                process=subprocess.Popen(['python3',str(ROOT/'deploy/install-tiles.py'),str(archive),str(stage),str(target)],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
                try:
                    time.sleep(.15)
                    self.assertIsNone(process.poll(),'installer must wait while another worker owns commit lock')
                    self.assertFalse((target/'a.gph').exists())
                finally:
                    fcntl.flock(lock,fcntl.LOCK_UN)
                    process.communicate(timeout=5)
                self.assertEqual(process.returncode,0)
                self.assertEqual((target/'a.gph').read_bytes(),b'first')


if __name__=='__main__': unittest.main()
