#!/usr/bin/env python3
"""Image promotion gate: original release bytes + constrained native execution.
Runs inside the built candidate image, never against production or cached API data.
"""
import json
from pathlib import Path
import subprocess
import tempfile
import time
import urllib.request

from regional_catalog import Catalog
from regional_download import build_plans, prepare_region, activate_region
from regional_engine import Engine
from regional_router import Router
from regional_release import validate_supply


def main():
    share = Path('/usr/local/share')
    tag = (share / 'anipals-tile-release.txt').read_text().strip()
    for attempt in range(3):
        try:
            with urllib.request.urlopen('https://api.github.com/repos/WILLJBS/anipals-tiles/releases/tags/' + tag, timeout=30) as response:
                release = json.load(response)
            break
        except OSError:
            if attempt == 2:
                raise
            time.sleep(2)
    roster = json.loads((share / 'anipals-regions.json').read_text())
    marker = next(a for a in release['assets'] if a['name'] == 'READY')
    with urllib.request.urlopen(marker['browser_download_url'], timeout=30) as response:
        ready_bytes = response.read()
    validate_supply(release, ready_bytes, roster, (share / 'anipals-valhalla-image.txt').read_text().strip(),
                    json.loads((share / 'anipals-coverage.json').read_text()))
    plans = build_plans(release, roster, (share / 'anipals-valhalla-image.txt').read_text().strip())
    plan = next(p for p in plans if p['slug'] == 'north-america-canada')
    template = json.loads(subprocess.check_output(['valhalla_build_config']))
    with tempfile.TemporaryDirectory(prefix='native-gate-') as root:
        descriptor = prepare_region(root, plan)
        engine = Engine(template, Path(root) / 'runtime')
        try:
            catalog = Catalog(root, json.loads((share / 'anipals-coverage.json').read_text()))
            router = Router(catalog, engine, json.loads((share / 'anipals-probes.json').read_text()))
            assert not catalog.available(), 'unverified graph exposed to routing'
            # Valhalla 3.3's CLI treats --version as a config filename. Its actual
            # status action is the supported binary-version inspection surface.
            status = engine.request(catalog.candidate(descriptor['slug'], descriptor['fingerprint']), 'status', {})
            assert isinstance(status, dict) and isinstance(status.get('version'), str), status
            print(json.dumps(dict(native_status=status)), flush=True)
            from native_diagnose import diagnose
            region = catalog.candidate(descriptor['slug'], descriptor['fingerprint'])
            diagnose(region, template, json.loads(engine.config(region).read_text()))
            result = router.verify(descriptor['slug'], descriptor['fingerprint'])
            activate_region(root, descriptor, result)
            for name, lat, lon in [('toronto', 43.7064, -79.3986), ('montreal', 45.5088, -73.5878)]:
                started = time.monotonic()
                result, region = router.route({'locations': [dict(lat=lat, lon=lon),
                                              dict(lat=lat+.002, lon=lon+.002)],
                                              'costing': 'pedestrian'})
                distance = result['trip']['summary']['length']
                assert 0 < distance < 5, result
                assert region['slug'] == 'north-america-canada'
                assert time.monotonic() - started < 8
                print(json.dumps(dict(probe=name, distance_km=distance,
                      elapsed_ms=round((time.monotonic()-started)*1000), fingerprint=region['fingerprint'])), flush=True)
            from native_remote_smoke import remote_smoke
            from regional_release import canonical_hash
            remote_smoke(region, json.loads(engine.config(region).read_text()), plan['image'],
                         canonical_hash(catalog.features[region['slug']]), root)
            assert router.status()['ready'] is True
            # Reuse completed bytes with zero downloader calls; no 416 after restart.
            def forbidden(*unused):
                raise AssertionError('complete graph downloaded again')
            assert prepare_region(root, plan, downloader=forbidden) == descriptor
        finally:
            engine.close()
    print('NATIVE_REGIONAL_GATE_PASSED', flush=True)


if __name__ == '__main__':
    main()
