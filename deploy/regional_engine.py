"""Bounded native requests: each process opens exactly one immutable graph.

The official valhalla_service CONFIG ACTION JSON interface provides the same
actor as HTTP mode. Process isolation also contains native crashes and releases
all memory after a request; the OS still caches immutable graph file pages.
"""
import json
import fcntl
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from regional_gc import safe_path


class EngineError(Exception):
    def __init__(self, message, status=503, *, native_code=None, native_exit_code=None):
        super().__init__(message)
        self.status = status
        self.native_code = native_code if type(native_code) is int else None
        self.native_exit_code = native_exit_code if type(native_exit_code) is int else None


class Engine:
    def __init__(self, template, runtime, concurrency=2, timeout=8, memory_mb=768, bridge=None):
        self.template = template
        self.bridge = bridge
        self.runtime = Path(runtime)
        self.runtime.mkdir(parents=True, exist_ok=True)
        self.slots = threading.BoundedSemaphore(concurrency)
        self.timeout, self.memory_mb = timeout, memory_mb
        self.lock = threading.Lock()
        self.children = set()
        self.closed = False

    def config(self, region, remote=None):
        path = self.runtime / ((remote[0] if remote else region['fingerprint']) + '.json')
        with self.lock:
            if not path.exists():
                config = json.loads(json.dumps(self.template))
                m = config['mjolnir']
                m.update(tile_dir='' if remote else region['tile_dir'], tile_extract='', traffic_extract='',
                         tile_url=remote[1] if remote else '', tile_url_gz=False,
                         max_cache_size=64 * 1024 * 1024, use_lru_mem_cache=False,
                         lru_mem_cache_hard_control=False)
                config['loki']['use_connectivity'] = False
                config['loki']['actions'] = ['route', 'locate', 'status']
                thor = config['thor']
                for key in list(thor):
                    if key.startswith('max_reserved_labels_count_'):
                        thor[key] = 100000
                thor['clear_reserved_memory'] = True
                temporary = path.with_suffix('.tmp')
                temporary.write_text(json.dumps(config))
                os.replace(temporary, path)
        return path

    def request(self, region, action, payload, timeout=None):
        if not self.slots.acquire(timeout=0.2):
            raise EngineError('native request capacity exhausted')
        child = None
        lease = None
        remote = None
        config = None
        started = time.monotonic()
        try:
            graph_root = Path(region['tile_dir']).parent
            try:
                safe_path(graph_root)
                lease = os.open(safe_path(graph_root / '.lease.lock'),
                                os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
                fcntl.flock(lease, fcntl.LOCK_SH | fcntl.LOCK_NB)
                marker = safe_path(graph_root / '.complete.json')
                safe_path(Path(region['tile_dir']))
            except (OSError, ValueError) as error:
                raise EngineError('graph lease unavailable during retirement') from error
            # A stale candidate must fail before spawning a native reader.
            if not Path(region['tile_dir']).is_dir() or not marker.is_file():
                raise EngineError('graph retired before request acquired its lease')
            identity = json.loads(marker.read_text())
            if identity.get('fingerprint') != region['fingerprint'] or identity.get('slug') != region['slug']:
                raise EngineError('graph identity changed before request')
            if region.get('storage') == 'r2':
                if self.bridge is None:
                    raise EngineError('remote graph transport unavailable')
                try:
                    remote = self.bridge.begin(region)
                except (ValueError, KeyError) as error:
                    raise EngineError('remote graph is not in trusted release') from error
            config = self.config(region, remote)
            command = [sys.executable, str(Path(__file__).with_name('regional_native.py')),
                       str(self.memory_mb), str(config), action, json.dumps(payload)]
            with self.lock:
                if self.closed:
                    raise EngineError('engine draining')
                child = subprocess.Popen(command, stdout=subprocess.PIPE,
                                         stderr=subprocess.PIPE, start_new_session=True,
                                         pass_fds=(lease,))
                self.children.add(child)
            try:
                stdout, stderr = child.communicate(timeout=min(self.timeout, timeout) if timeout is not None else self.timeout)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.communicate()
                raise EngineError('native request deadline exceeded')
            if remote and self.bridge.failed(remote[0]):
                raise EngineError('remote graph object retrieval failed')
            if child.returncode:
                # Coordinates and full native request/output never enter logs.
                print(json.dumps({'event': 'native_failure', 'region': region['slug'],
                                  'exit': child.returncode}), flush=True)
                try:
                    failure = json.loads(stdout)
                except (ValueError, UnicodeError):
                    failure = {}
                code = failure.get('error_code') if isinstance(failure, dict) else None
                if code in (170, 171, 172, 442, 443, 444):
                    raise EngineError('no suitable route in regional graph', 404,
                                      native_code=code, native_exit_code=child.returncode)
                raise EngineError('native graph request failed', native_code=code,
                                  native_exit_code=child.returncode)
            try:
                result = json.loads(stdout)
            except (ValueError, UnicodeError):
                raise EngineError('native response is not JSON')
            print(json.dumps({'event': 'native_request', 'region': region['slug'],
                              'action': action, 'ms': round((time.monotonic()-started)*1000)}), flush=True)
            return result
        finally:
            if remote:
                self.bridge.end(remote[0])
                if config is not None:
                    config.unlink(missing_ok=True)
            # Do not explicitly LOCK_UN: the inherited native descriptor keeps
            # the lease even if the Python router dies before its child exits.
            if lease is not None:
                os.close(lease)
            with self.lock:
                self.children.discard(child)
            self.slots.release()

    def close(self):
        with self.lock:
            self.closed = True
            children = list(self.children)
        for child in children:
            if child.poll() is None:
                try:
                    os.killpg(child.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        deadline = time.monotonic() + 2
        for child in children:
            try:
                child.wait(timeout=max(.01, deadline-time.monotonic()))
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                child.wait()
