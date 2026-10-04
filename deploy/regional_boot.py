"""PID1 supervises HTTP and a resumable, pinned-release materializer."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
import urllib.request

from regional_release import validate_supply

BASE = Path(__file__).parent
SHARE = Path('/usr/local/share')


def main():
    stop = threading.Event()
    processes = []
    lock = threading.Lock()

    def spawn(args):
        with lock:
            if stop.is_set():
                return None
            process = subprocess.Popen(args, start_new_session=True)
            processes.append(process)
            return process

    def terminate(*unused):
        stop.set()
        with lock:
            for process in processes:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)

    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)
    router = spawn([sys.executable, str(BASE / 'regional_router.py')])

    def materialize():
        # Index ownership must be installed before any local worker starts.
        expected_index = os.environ.get('ANIPALS_REMOTE_INDEX_SHA256')
        while expected_index and not stop.is_set():
            try:
                with urllib.request.urlopen('http://127.0.0.1:8002/status', timeout=5) as response:
                    status = json.load(response)
                if status.get('ownership_index') == expected_index:
                    break
            except (OSError, ValueError):
                pass
            stop.wait(2)
        if stop.is_set():
            return
        # Release changes are code-reviewed, not implicit GitHub latest changes.
        tag = (SHARE / 'anipals-tile-release.txt').read_text().strip()
        target = Path('/data/regions') / ('release-' + tag + '.json')
        ready_target = target.with_suffix('.ready')
        target.parent.mkdir(parents=True, exist_ok=True)
        def validate(release, ready_bytes):
            validate_supply(release, ready_bytes,
                json.loads((SHARE / 'anipals-regions.json').read_text()),
                (SHARE / 'anipals-valhalla-image.txt').read_text().strip(),
                json.loads((SHARE / 'anipals-coverage.json').read_text()))
        while not stop.is_set():
            if not target.exists() or not ready_target.exists():
                temporary = target.with_suffix('.tmp')
                fetch = spawn(['curl', '-sfL', '--retry', '3', '--retry-delay', '2',
                               '--max-time', '60',
                               'https://api.github.com/repos/WILLJBS/anipals-tiles/releases/tags/' + tag,
                               '-o', str(temporary)])
                if fetch is None:
                    return
                if fetch.wait() == 0:
                    try:
                        release = json.loads(temporary.read_text())
                        if release['tag_name'] != tag:
                            raise ValueError('release tag mismatch')
                        marker = next(a for a in release['assets'] if a['name'] == 'READY')
                        with urllib.request.urlopen(marker['browser_download_url'], timeout=30) as response:
                            ready_bytes = response.read(16 * 1024 * 1024 + 1)
                        validate(release, ready_bytes)
                        ready_temporary = ready_target.with_suffix('.ready.tmp')
                        ready_temporary.write_bytes(ready_bytes)
                        os.replace(ready_temporary, ready_target)
                        os.replace(temporary, target)
                    except (KeyError, ValueError, OSError, StopIteration):
                        print('[regional] rejected release metadata', flush=True)
            if target.exists() and ready_target.exists():
                try:
                    validate(json.loads(target.read_text()), ready_target.read_bytes())
                except (KeyError, ValueError, OSError):
                    target.unlink(missing_ok=True)
                    ready_target.unlink(missing_ok=True)
                    print('[regional] invalid cached supply; refetching metadata', flush=True)
                    stop.wait(30)
                    continue
                worker = spawn([sys.executable, str(BASE / 'regional_download.py'),
                                '--release-json', str(target)])
                if worker is None:
                    return
                code = worker.wait()
                if code == 0:
                    print('[regional] all graphs materialized', flush=True)
                    return
                print('[regional] materializer exited; resuming after cooldown', flush=True)
            stop.wait(30)

    thread = threading.Thread(target=materialize, daemon=True)
    thread.start()
    try:
        next_collection = time.monotonic()
        while not stop.wait(1):
            if router.poll() is not None:
                raise RuntimeError('regional HTTP process exited')
            if time.monotonic() >= next_collection:
                from regional_gc import collect_retired
                try:
                    collect_retired('/data')
                except (OSError, ValueError, KeyError, TypeError) as error:
                    # A retirement failure must remain visible and retryable;
                    # it must not restart healthy readers of the active graph.
                    print(json.dumps({'event': 'retired_graph_collection_failed',
                                      'error': str(error)}), flush=True)
                next_collection = time.monotonic() + 30
    finally:
        terminate()
        deadline = time.monotonic() + 10
        with lock:
            children = list(processes)
        for process in children:
            try:
                process.wait(timeout=max(0.1, deadline-time.monotonic()))
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()


if __name__ == '__main__':
    main()
