"""Private Valhalla-compatible router; graph files never cross region roots."""
import argparse
import http.server
import json
import os
from pathlib import Path
import signal
import threading
import time
import urllib.parse

from regional_catalog import Catalog, locations
from regional_engine import Engine, EngineError
from regional_probes import graph_points


class Router:
    def __init__(self, catalog, engine, probes):
        self.catalog, self.engine, self.probes = catalog, engine, probes
        self.verified = set()
        self.lock = threading.Lock()

    def mark_verified(self, region):
        with self.lock:
            self.verified.add((region['slug'], region['fingerprint']))

    def status(self):
        regions = self.catalog.available()
        with self.lock:
            verified = [dict(slug=s, fingerprint=r['fingerprint'], verified=True)
                        for s, r in regions.items() if (s, r['fingerprint']) in self.verified]
        return {'architecture': 'regional-v1', 'revision': os.environ.get('ANIPALS_REVISION', 'local'),
                'release': self.catalog.fingerprint(regions), 'ready': bool(verified),
                'complete': len(regions) == len(self.catalog.features),
                'region_count': len(regions), 'expected_regions': len(self.catalog.features),
                'available_regions': sorted(regions), 'ready_regions': verified}

    def route(self, payload):
        points = locations(payload)
        candidates = self.catalog.candidates(points)
        if not candidates:
            raise EngineError('no single extract covers both locations', 404)
        regions = self.catalog.available()
        # A broad remote extract must not delay an already installed country graph.
        candidates.sort(key=lambda slug: (regions.get(slug, {}).get('storage') == 'r2', slug))
        missing = any(slug not in regions for slug in candidates)
        failure = EngineError('regional graph is downloading')
        unavailable = None
        deadline = time.monotonic() + 8
        for slug in candidates:
            region = regions.get(slug)
            if region is None:
                continue
            try:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise EngineError('regional request deadline exceeded')
                # Accept only the established API payload. Do not expose native
                # administrative/options surfaces on the private HTTP listener.
                request = {'locations': [{'lon': x, 'lat': y} for x, y in points],
                           'costing': 'pedestrian', 'directions_type': 'none'}
                result = self.engine.request(region, 'route', request, timeout=remaining)
                if not isinstance(result, dict):
                    raise EngineError('invalid native route response')
                trip = result.get('trip', {})
                if not isinstance(trip, dict) or not trip.get('legs') or not isinstance(trip.get('summary'), dict):
                    raise EngineError('invalid native route response')
                length = trip['summary'].get('length')
                if type(length) not in (int, float) or length <= 0:
                    raise EngineError('invalid native route response')
                self.mark_verified(region)
                return result, region
            except EngineError as error:
                failure = error
                if error.status >= 500:
                    unavailable = error
        if missing:
            raise EngineError('candidate regional graph is downloading')
        raise unavailable or failure

    def verify(self, slug, fingerprint):
        region = self.catalog.candidate(slug, fingerprint)
        samples = [p for p in self.probes if slug in self.catalog.candidates([(p['lng'], p['lat'])])]
        # Canada reproduces both historical crashes. Other extracts receive a
        # deterministic city probe, or actual graph nodes if no city is registered.
        if slug == 'north-america-canada':
            samples = [p for p in samples if p['slug'] in ('toronto', 'montreal')]
        else:
            samples = samples[:1]
        def check(sample):
            payload = {'locations': [{'lat': sample['lat'], 'lon': sample['lng']}],
                       'costing': 'pedestrian', 'verbose': True}
            result = self.engine.request(region, 'locate', payload)
            if not isinstance(result, list) or not result or not isinstance(result[0], dict):
                raise EngineError('invalid native probe response')
            if not result[0].get('edges'):
                raise EngineError('native probe found no edges', 404)
        if region.get('storage') == 'r2':
            for sample in region['probes']:
                check(sample)
        elif slug == 'north-america-canada':
            if len(samples) != 2 or {p['slug'] for p in samples} != {'toronto', 'montreal'}:
                raise EngineError('required regression probes missing')
            for sample in samples:
                check(sample)
        else:
            # A city's center can legitimately have no walking edge. Check real
            # accessible graph nodes before deciding the entire graph is unhealthy.
            from itertools import chain
            for sample in chain(samples, graph_points(region['tile_dir'], limit=3)):
                try:
                    check(sample)
                    break
                except EngineError as error:
                    if error.status != 404:
                        raise
            else:
                raise EngineError('no readable pedestrian edges in regional graph')
        self.mark_verified(region)
        return {'slug': slug, 'fingerprint': fingerprint, 'verified': True}


class Handler(http.server.BaseHTTPRequestHandler):
    def send(self, status, result, region=None):
        body = json.dumps(result, separators=(',', ':')).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        if region:
            self.send_header('X-Anipals-Region', region['slug'])
            self.send_header('X-Anipals-Graph', region['fingerprint'])
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        if self.path != '/status':
            self.send(404, {'error': 'not found'})
            return
        try:
            self.send(200, self.server.router.status())
        except (ValueError, OSError, KeyError):
            self.send(503, {'error': 'invalid graph installation state'})

    def do_POST(self):
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if size <= 0 or size > 8192:
                self.send(413, {'error': 'invalid request size'})
                return
            payload = json.loads(self.rfile.read(size))
            if not isinstance(payload, dict):
                raise ValueError('invalid request')
            if self.path == '/route':
                result, region = self.server.router.route(payload)
                self.send(200, result, region)
            elif self.path == '/verify' and self.client_address[0] in ('127.0.0.1', '::1'):
                self.send(200, self.server.router.verify(payload['slug'], payload['fingerprint']))
            else:
                self.send(404, {'error': 'not found'})
        except EngineError as error:
            self.send(error.status, {'error': str(error), 'error_code': 'NAVIGATION_UNAVAILABLE'})
        except (ValueError, KeyError, TypeError):
            self.send(400, {'error': 'invalid request'})
        except OSError:
            self.send(503, {'error': 'graph IO unavailable'})

    def setup(self):
        super().setup()
        self.connection.settimeout(12)

    def log_message(self, *args):
        pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-root', default='/data')
    parser.add_argument('--coverage', default='/usr/local/share/anipals-coverage.json')
    parser.add_argument('--probes', default='/usr/local/share/anipals-probes.json')
    parser.add_argument('--config', default='/tmp/anipals-config.json')
    parser.add_argument('--port', type=int, default=8002)
    args = parser.parse_args()
    coverage = json.loads(Path(args.coverage).read_text())
    remote = None
    if os.environ.get('ANIPALS_REMOTE_INDEX_SHA256'):
        from regional_remote_runtime import RemoteRuntime
        remote = RemoteRuntime(args.data_root, coverage)
        coverage = remote.composite.coverage
    engine = Engine(json.loads(Path(args.config).read_text()), '/tmp/anipals-native',
                    bridge=remote.bridge if remote else None)
    router = Router(Catalog(args.data_root, coverage),
                    engine, json.loads(Path(args.probes).read_text()))
    if remote:
        remote.start(router)
    server = http.server.ThreadingHTTPServer(('0.0.0.0', args.port), Handler)
    server.router = router
    def stop(*unused):
        engine.close()
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print(json.dumps({'event': 'regional_router_listening', 'port': args.port}), flush=True)
    try:
        server.serve_forever()
    finally:
        engine.close()
        server.server_close()
        if remote:
            remote.close()


if __name__ == '__main__':
    main()
