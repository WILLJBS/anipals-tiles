"""CI-only fixed public-coordinate matrix; never called on user requests."""
import json
from pathlib import Path
import subprocess
import sys
import threading
import time


def diagnose(region, template, constrained):
    normal = json.loads(json.dumps(template))
    normal['mjolnir'].update(tile_dir=region['tile_dir'], tile_extract='', traffic_extract='')
    normal['loki']['use_connectivity'] = False
    hard = json.loads(json.dumps(constrained))
    hard['mjolnir'].update(use_lru_mem_cache=True, lru_mem_cache_hard_control=True)
    soft = json.loads(json.dumps(hard))
    soft['mjolnir']['lru_mem_cache_hard_control'] = False
    no_lru = json.loads(json.dumps(hard))
    no_lru['mjolnir']['use_lru_mem_cache'] = False
    variants = [('default-4096', normal, 4096), ('default-768', normal, 768),
                ('hard-lru-4096', hard, 4096), ('hard-lru-768', hard, 768),
                ('soft-lru-768', soft, 768), ('no-lru-768', no_lru, 768)]
    results = []
    for city, lat, lon in [('montreal',45.5088,-73.5878), ('toronto',43.7064,-79.3986)]:
      request = json.dumps({'locations': [{'lat': lat, 'lon': lon}], 'costing': 'pedestrian', 'verbose': True})
      for label, config, memory in variants:
        path = Path(region['tile_dir']).parent / (label+'.json')
        path.write_text(json.dumps(config))
        command = [sys.executable, str(Path(__file__).with_name('regional_native.py')),
                   str(memory), str(path), 'locate', request]
        started = time.monotonic()
        child = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        peak = {'rss_kib': 0, 'vms_kib': 0}
        def sample():
            while child.poll() is None:
                try:
                    for line in Path('/proc/%d/status' % child.pid).read_text().splitlines():
                        for prefix, key in [('VmRSS:', 'rss_kib'), ('VmSize:', 'vms_kib')]:
                            if line.startswith(prefix):
                                peak[key] = max(peak[key], int(line.split()[1]))
                except (OSError, ValueError):
                    pass
                time.sleep(.02)
        monitor = threading.Thread(target=sample, daemon=True)
        monitor.start()
        try:
            out, err = child.communicate(timeout=25)
        except subprocess.TimeoutExpired:
            child.kill(); out, err = child.communicate()
        monitor.join(2)
        results.append(dict(city=city, matrix=label, exit=child.returncode))
        print(json.dumps(dict(city=city, matrix=label, exit=child.returncode, seconds=round(time.monotonic()-started, 3),
                              peak=peak, stdout=out.decode(errors='replace')[:200],
                              stderr=err.decode(errors='replace')[-3000:])), flush=True)
    assert all(r['exit'] == 0 for r in results if r['matrix'] == 'no-lru-768'), results
