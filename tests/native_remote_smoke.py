"""Real pinned native cold/hot tile_url gate using verified local object bytes.

No cloud credentials: this proves 3.3.0 HTTP semantics, isolation and bounds.
Real R2 latency/error acceptance remains a separate deployment gate.
"""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

from regional_object_bridge import Bridge
from regional_object_cache import ObjectCache
from regional_objects import ObjectCatalog
from regional_release import canonical_hash


def remote_smoke(region, config, image, coverage_sha256, root):
    root = Path(root) / 'remote-gate'
    root.mkdir()
    tiles = Path(region['tile_dir'])
    inventory = {}
    for file in sorted(tiles.rglob('*.gph')):
        digest = hashlib.sha256()
        with file.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(chunk)
        inventory[file.relative_to(tiles).as_posix()] = dict(size=file.stat().st_size, sha256=digest.hexdigest())
    fingerprint = canonical_hash({p: t['sha256'] for p, t in inventory.items()})
    manifest = dict(schema=1, validation='gph-v3-index-v1', slug=region['slug'], image=image,
                    graph_fingerprint=fingerprint, coverage_sha256=coverage_sha256, tiles=inventory)
    raw = json.dumps(manifest).encode()
    catalog = ObjectCatalog([(raw, hashlib.sha256(raw).hexdigest())], image)
    prefix = 'navigation/graphs/%s/%s/tiles/' % (region['slug'], fingerprint)
    fetched = []
    def fetch(key):
        assert key.startswith(prefix), 'cross-graph object request'
        path = key[len(prefix):]
        assert path in inventory, 'object request outside declared inventory'
        fetched.append(path)
        with (tiles / path).open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                yield chunk
    cache = ObjectCache(root / 'objects', 512 * 1024 * 1024)
    bridge = Bridge(catalog, cache, fetch).start()
    try:
        config = json.loads(json.dumps(config))
        config['mjolnir'].update(tile_dir='', tile_extract='', traffic_extract='',
            tile_url=bridge.url(region['slug'], fingerprint), tile_url_gz=False)
        config_path = root / 'native.json'
        config_path.write_text(json.dumps(config))
        native = Path(__file__).with_name('regional_native.py')
        for name, lat, lon in [('toronto', 43.7064, -79.3986), ('montreal', 45.5088, -73.5878)]:
            payload = {'locations': [dict(lat=lat, lon=lon), dict(lat=lat+.002, lon=lon+.002)], 'costing': 'pedestrian'}
            for temperature in ('cold', 'hot'):
                before, started = len(fetched), time.monotonic()
                result = subprocess.run([sys.executable, str(native), '768', str(config_path), 'route', json.dumps(payload)],
                                        cwd=root, capture_output=True, timeout=8, check=True)
                response = json.loads(result.stdout)
                distance = response['trip']['summary']['length']
                assert 0 < distance < 5, response
                assert bridge.failures == 0, 'verified bridge failed'
                if temperature == 'hot':
                    assert before == len(fetched), 'warm route fetched object again'
                print(json.dumps(dict(remote_probe=name, cache=temperature,
                    distance_km=distance, object_fetches=len(fetched)-before,
                    elapsed_ms=round((time.monotonic()-started)*1000))), flush=True)
        assert fetched, 'native never fetched remote tiles'
        assert not list(root.rglob('*.gph')), 'native bypassed bounded object cache'
    finally:
        bridge.close()
        cache.close()
    print('NATIVE_REMOTE_TILE_GATE_PASSED', flush=True)
