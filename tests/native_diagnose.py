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
    request = json.dumps({'locations': [{'lat': 43.7064, 'lon': -79.3986}],
                          'costing': 'pedestrian', 'verbose': True})
    for label, config, memory in [('default-4096', normal, 4096), ('default-768', normal, 768),
                                   ('configured-4096', constrained, 4096), ('configured-768', constrained, 768)]:
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
        print(json.dumps(dict(matrix=label, exit=child.returncode, seconds=round(time.monotonic()-started, 3),
                              peak=peak, stdout=out.decode(errors='replace')[:200],
                              stderr=err.decode(errors='replace')[-3000:])), flush=True)
