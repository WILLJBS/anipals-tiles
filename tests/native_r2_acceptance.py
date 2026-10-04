#!/usr/bin/env python3
"""Read-only real R2/native gate; private inputs/results, no active pointers."""
import argparse
from contextlib import ExitStack
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import time


def require(ok, code):
    if not ok:
        raise ValueError(code)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def pinned_file(path, expected, maximum):
    require(re.fullmatch('[0-9a-f]{64}', expected), 'INVALID_INPUT_SHA')
    with Path(path).open('rb') as stream:
        raw = stream.read(maximum + 1)
    require(len(raw) <= maximum and digest(raw) == expected, 'INPUT_SHA_OR_SIZE_MISMATCH')
    return raw


# The CLI adds its exact checkout tools path before calling these shared helpers.
def verify_receipt(*args, **kwargs):
    from migration_receipts import verify_receipt as shared
    return shared(*args, **kwargs)


def verify_manifest(*args, **kwargs):
    from migration_receipts import verify_manifest as shared
    return shared(*args, **kwargs)


def probe_payload(raw, feature, slug):
    from regional_catalog import contains, locations
    value = json.loads(raw)
    require(set(value) == {'schema', 'slug', 'provenance', 'payload'} and value['schema'] == 1
            and value['slug'] == slug and isinstance(value['provenance'], str)
            and 0 < len(value['provenance']) <= 1000, 'UNREVIEWED_PROBE')
    payload = value['payload']
    require(set(payload) == {'locations', 'costing'}
            and all(set(point) == {'lat', 'lon'} for point in payload['locations']),
            'PROBE_MUST_USE_RUNTIME_DEFAULTS')
    points = locations(payload)
    require(points[0] != points[1] and all(contains(feature['geometry'], p) for p in points),
            'PROBE_OUTSIDE_DECLARED_SCOPE')
    return payload


class Counter:
    def __init__(self):
        self.lock = threading.Lock()
        self.http, self.fetches, self.bytes = 0, 0, 0

    def before_send(self, **_):
        # Count actual SDK HTTP attempts, including retries. Never inspect requests.
        with self.lock:
            self.http += 1

    def snapshot(self):
        with self.lock:
            return self.http, self.fetches, self.bytes

    def tile_reader(self, fetch, prefix, inventory):
        def read(key):
            require(key.startswith(prefix) and key[len(prefix):] in inventory, 'CROSS_GRAPH_GET_REFUSED')
            with self.lock:
                self.fetches += 1
            chunks = iter(fetch(key))
            try:
                for chunk in chunks:
                    with self.lock:
                        self.bytes += len(chunk)
                    yield chunk
            finally:
                close = getattr(chunks, 'close', None)
                if close:
                    close()
        return read


def route_pair(engine, region, payload, counter, bridge):
    records, shapes = [], []
    for temperature in ('cold', 'hot'):
        before, start = counter.snapshot(), time.monotonic()
        result = engine.request(region, 'route', payload)
        elapsed = time.monotonic() - start
        after = counter.snapshot()
        trip = result['trip']
        length = trip['summary']['length']
        require(type(length) in (int, float) and math.isfinite(length) and 0 < length < 5
                and trip.get('units') == 'kilometers' and elapsed < 8 and bridge.failures == 0,
                'NATIVE_ROUTE_OR_BOUNDS_FAILED')
        shape = [leg['shape'] for leg in trip['legs']]
        require(shape and all(isinstance(s, str) and s for s in shape), 'NATIVE_GEOMETRY_MISSING')
        delta = tuple(a-b for a, b in zip(after, before))
        require((delta[0] > 0 and delta[1] > 0 and delta[2] > 0) if temperature == 'cold'
                else delta == (0, 0, 0), 'COLD_NOT_R2_OR_HOT_SOURCE_IO')
        shapes.append((length, shape))
        records.append(dict(cache=temperature, http_gets=delta[0], object_fetches=delta[1],
                            source_bytes=delta[2], elapsed_ms=round(elapsed*1000), distance_km=length))
    require(shapes[0] == shapes[1], 'COLD_HOT_ROUTE_DIFFERS')
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('checkout', 'diagnostic-sha', 'migration-sha', 'contract', 'tag', 'slug',
                 'release', 'release-sha', 'ready', 'ready-sha', 'receipt-sha', 'manifest-sha',
                 'probe', 'probe-sha', 'graph-fingerprint', 'output'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--receipt-size', type=int, required=True)
    parser.add_argument('--manifest-size', type=int, required=True)
    args = parser.parse_args()
    root = Path(args.checkout).resolve()
    sys.path[:0] = [str(root/'deploy'), str(root/'tools')]
    from migration_contract import load_profile, source_revision, validate_profile_supply
    from migration_ci import receipt_key
    from navigation_catalog_inputs import decode
    from regional_composite import read_object
    from regional_engine import Engine
    from regional_object_cache import ObjectCache
    from regional_object_bridge import Bridge
    import regional_r2
    source_revision(args.diagnostic_sha, root=root)
    require(not subprocess.check_output(['git', 'status', '--porcelain'], cwd=root),
            'DIAGNOSTIC_CHECKOUT_DIRTY')
    require(os.environ.get('ANIPALS_REVISION') == args.diagnostic_sha, 'CANDIDATE_IMAGE_REVISION_DIFFERS')
    require(re.fullmatch('[0-9a-f]{40}', args.migration_sha)
            and 0 < args.receipt_size <= 8*1024*1024, 'INVALID_RECEIPT_INPUT')
    profile = load_profile(args.contract, root=root)
    release = decode(pinned_file(args.release, args.release_sha, 16*1024*1024))
    require(release.get('tag_name') == args.tag, 'RELEASE_TAG_DIFFERS')
    ready = pinned_file(args.ready, args.ready_sha, 4*1024*1024)
    plans = validate_profile_supply(profile, release, ready)
    plan = next(p for p in plans if p['slug'] == args.slug)
    key = receipt_key(args.migration_sha, args.tag, 'receipt-' + args.slug, args.receipt_sha)
    probe_raw = pinned_file(args.probe, args.probe_sha, 8192)
    counter = Counter()
    with ExitStack() as stack:
        client, bucket = regional_r2.connection()
        stack.callback(client.close)
        client.meta.events.register('before-send.s3.GetObject', counter.before_send)
        # Inject the same real client into the existing runtime reader to count HTTP
        # attempts and close it deterministically; transport/config stay unchanged.
        original = regional_r2.connection
        try:
            regional_r2.connection = lambda *_: (client, bucket)
            fetch = regional_r2.reader()
        finally:
            regional_r2.connection = original
        receipt = decode(read_object(fetch, key, args.receipt_sha, 8*1024*1024, args.receipt_size))
        feature = verify_receipt(receipt, args, profile, plans, release, bucket)
        require(receipt['graph_fingerprint'] == args.graph_fingerprint
                and receipt['manifest_size'] == args.manifest_size, 'REQUEST_MANIFEST_IDENTITY_DIFFERS')
        prefix = 'navigation/graphs/%s/%s/' % (args.slug, receipt['graph_fingerprint'])
        raw = read_object(fetch, prefix+'manifests/'+args.manifest_sha+'.json',
                          args.manifest_sha, 32*1024*1024, receipt['manifest_size'])
        ready_value = {} if ready == b'ok\n' else decode(ready)
        ready_region = ready_value.get('region_manifests', {}).get(args.slug)
        catalog, manifest = verify_manifest(raw, args, receipt, feature, profile, plan, ready_region)
        expected_graph = ready_value.get('region_manifests', {}).get(args.slug, {}).get('graph_fingerprint')
        require(expected_graph is None or expected_graph == receipt['graph_fingerprint'], 'READY_GRAPH_DIFFERS')
        payload = probe_payload(probe_raw, feature, args.slug)
        temp = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='anipals-r2-gate-'))).resolve()
        graph = temp/'graph'; (graph/'tiles').mkdir(parents=True)
        region = dict(slug=args.slug, fingerprint=receipt['graph_fingerprint'],
                      object_fingerprint=receipt['graph_fingerprint'], storage='r2', tile_dir=str(graph/'tiles'))
        (graph/'.complete.json').write_text(json.dumps(region))
        cache = ObjectCache(temp/'cache', 512*1024*1024)
        stack.callback(cache.close)
        bridge = Bridge(catalog, cache, counter.tile_reader(fetch, prefix+'tiles/', manifest['tiles'])).start()
        stack.callback(bridge.close)
        template = json.loads(subprocess.check_output(['valhalla_build_config'], timeout=30))
        engine = Engine(template, temp/'native', concurrency=1, timeout=8, memory_mb=768, bridge=bridge)
        stack.callback(engine.close)
        status = engine.request(region, 'status', {})
        require(status.get('version') == '3.3.0', 'NATIVE_ABI_DIFFERS')
        require(counter.snapshot()[1:] == (0, 0), 'STATUS_WARMED_CACHE')
        records = route_pair(engine, region, payload, counter, bridge)
        cache_bytes = sum(p.stat().st_size for p in (temp/'cache').iterdir() if re.fullmatch('[0-9a-f]{64}', p.name))
        require(cache_bytes <= 512*1024*1024 and not list(temp.rglob('*.gph'))
                and not list(temp.rglob('active.json')), 'UNBOUNDED_OR_ACTIVE_GRAPH_WRITE')
        result = dict(schema=1, event='REAL_R2_NATIVE_COLD_HOT_PASSED', diagnostic_sha=args.diagnostic_sha,
                      migration_sha=args.migration_sha, receipt_sha256=args.receipt_sha,
                      manifest_sha256=args.manifest_sha, probe_sha256=args.probe_sha,
                      graph_fingerprint=receipt['graph_fingerprint'], cache_bytes=cache_bytes,
                      memory_mb=768, timeout_seconds=8, records=records, production_activated=False)
    with Path(args.output).open('x') as stream:
        json.dump(result, stream, sort_keys=True)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps(dict(event='REAL_R2_NATIVE_GATE_FAILED', error=type(error).__name__)), file=sys.stderr)
        raise SystemExit(1) from None
