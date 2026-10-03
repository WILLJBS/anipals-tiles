import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'deploy'))
from regional_engine import Engine, EngineError
from regional_object_bridge import Bridge
from regional_object_cache import ObjectCache
from test_regional_objects import inventory, catalog, PATH


class RemoteEngineTests(unittest.TestCase):
    def test_actual_child_http_failure_cannot_become_native_no_route_404(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name).resolve()
            native = root / 'valhalla_service'
            native.write_text('''#!/usr/bin/env python3
import json, sys, urllib.request
config=json.load(open(sys.argv[1]))
try:
    url=config['mjolnir']['tile_url'].replace('{tilePath}', '2/000/000/001.gph')
    with urllib.request.urlopen(url,timeout=2) as r: r.read()
except Exception:
    print(json.dumps({'error_code':442})); sys.exit(1)
print(json.dumps({'ok':True}))
''')
            native.chmod(0o755)
            value = inventory('a', b'good')
            objects = catalog(value)
            cache = ObjectCache(root / 'cache', 8, 0)
            broken = [True]
            def fetch(_):
                yield b'bad!' if broken[0] else b'good'
            bridge = Bridge(objects, cache, fetch).start()
            tiles = root / 'graph/tiles'; tiles.mkdir(parents=True)
            region = dict(slug='a', fingerprint='a'*64, storage='r2',
                          object_fingerprint=value['graph_fingerprint'], tile_dir=str(tiles))
            (tiles.parent / '.complete.json').write_text(json.dumps(region))
            engine = Engine(dict(mjolnir={}, loki={}, thor={}), root / 'configs', bridge=bridge)
            launch = subprocess.Popen
            def start(command, **kwargs):
                if sys.platform == 'darwin':
                    command = [str(native)] + command[3:]
                return launch(command, **kwargs)
            try:
                with patch.dict(os.environ, PATH=str(root)+os.pathsep+os.environ['PATH']), patch('regional_engine.subprocess.Popen', side_effect=start):
                    with self.assertRaises(EngineError) as failure:
                        engine.request(region, 'route', {})
                    self.assertEqual(failure.exception.status, 503)
                    broken[0] = False
                    self.assertEqual(engine.request(region, 'route', {}), {'ok': True})
                    self.assertEqual(bridge.requests, {})
                    self.assertEqual(list((root / 'configs').glob('*.json')), [])
            finally:
                engine.close(); bridge.close(); cache.close()


if __name__ == '__main__':
    unittest.main()
