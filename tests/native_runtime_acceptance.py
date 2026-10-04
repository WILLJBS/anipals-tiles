#!/usr/bin/env python3
"""Candidate isolated native session CLI; controlled volume preparation is external."""
import argparse
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'tools'), str(ROOT/'deploy')]
from native_r2_acceptance import Counter, pinned_file, probe_payload, require
from native_r2_contract import execution
from navigation_catalog_inputs import LocalObjects, decode
from runtime_index_assembler import assemble, canonical_bytes
from plan_runtime_index import write_private
from regional_composite import Composite
from regional_engine import Engine
from regional_object_bridge import Bridge
from regional_object_cache import ObjectCache
from native_runtime_session import exercise, check_restart


def isolated_volume(path, runner_temp):
    require(runner_temp, 'TRUSTED_RUNNER_TEMP_REQUIRED')
    root, temp = Path(path), Path(runner_temp)
    require(root.is_absolute() and temp.is_absolute() and root.resolve(strict=True) == root
            and temp.resolve(strict=True) == temp and root != temp and temp in root.parents,
            'VOLUME_MUST_BE_CANONICAL_RUNNER_TEMP_CHILD')
    require(root.is_dir() and not root.is_symlink(), 'UNSAFE_ISOLATED_VOLUME')
    return root


def composite_for(index, coverage, objects):
    manifests = {}
    for row in index['remote_regions']:
        key = 'navigation/graphs/%s/%s/manifests/%s.json' % (
            row['slug'], row['graph_fingerprint'], row['manifest_sha256'])
        ref = dict(key=key, sha256=row['manifest_sha256'], bytes=row['manifest_size'])
        manifests[row['slug']] = objects.read(ref, 32*1024*1024, key)
    raw = canonical_bytes(index)
    return Composite(raw, hashlib.sha256(raw).hexdigest(), coverage, index['image'], manifests)


def checked_bucket(actual, request_raw, objects):
    request = decode(request_raw)
    catalog = decode(objects.read(request['catalog'], 32*1024*1024))
    require(actual == catalog['bucket'], 'R2_BUCKET_DIFFERS_FROM_REVERIFIED_CATALOG')


def native_engine(scratch, stack, bridge=None):
    template = json.loads(subprocess.check_output(['valhalla_build_config'], timeout=30))
    engine = Engine(template, scratch, concurrency=1, timeout=8, memory_mb=768, bridge=bridge)
    stack.callback(engine.close)
    return engine


def run_restart(state, folder, source_sha):
    state_path = folder/'restart-input.json'
    raw = canonical_bytes(state); write_private(state_path, raw)
    output = folder/'restart-output.json'
    command = [sys.executable, str(Path(__file__).resolve()), '--source-sha', source_sha,
               '--restart-state', str(state_path), '--restart-sha', hashlib.sha256(raw).hexdigest(),
               '--output', str(output)]
    env = {k: v for k, v in os.environ.items() if not k.startswith('R2_')}
    subprocess.run(command, env=env, check=True, timeout=90, stdout=subprocess.DEVNULL)
    require(output.stat().st_size <= 8192, 'RESTART_RESULT_TOO_LARGE')
    value = decode(output.read_bytes())
    wanted = dict(index_sha256=state['expected']['index_sha256'],
                  local_fingerprint=state['expected']['local_fingerprint'],
                  restart_verified=True, production_activated=False)
    require(value == wanted, 'FRESH_PROCESS_RESTART_RESULT_DIFFERS')
    return value


def checked_execution(source_sha):
    context = execution(source_sha)
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    require(head == source_sha and not subprocess.check_output(
        ['git', 'status', '--porcelain'], cwd=ROOT), 'RUNNER_CHECKOUT_DIFFERS_OR_DIRTY')
    require(os.environ.get('ANIPALS_REVISION') == source_sha, 'CANDIDATE_IMAGE_REVISION_DIFFERS')
    return context


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-sha', required=True)
    parser.add_argument('--output', type=Path, required=True)
    for name in ('request', 'request-sha', 'objects', 'volume', 'probe', 'probe-sha',
                 'restart-state', 'restart-sha'):
        parser.add_argument('--'+name)
    args = parser.parse_args()
    context = checked_execution(args.source_sha)
    with ExitStack() as stack:
        scratch = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='native-runtime-')))
        if args.restart_state:
            require(args.restart_sha and all(getattr(args, n) is None for n in
                    ('request','request_sha','objects','volume','probe','probe_sha')), 'EXACT_RESTART_ARGUMENTS_REQUIRED')
            state = decode(pinned_file(args.restart_state, args.restart_sha, 16*1024*1024))
            require(set(state) == {'index','coverage','objects','volume','volume_identity','payload','expected'},
                    'EXACT_RESTART_STATE_REQUIRED')
            root = isolated_volume(state['volume'], os.environ.get('RUNNER_TEMP'))
            stat = root.stat()
            require(state['volume_identity'] == [stat.st_dev, stat.st_ino], 'RESTART_VOLUME_SUBSTITUTED')
            composite = composite_for(state['index'], state['coverage'], LocalObjects(state['objects']))
            require(composite.digest == state['expected']['index_sha256'], 'RESTART_INDEX_CHANGED')
            result = check_restart(composite, root, native_engine(scratch/'native', stack),
                                   state['payload'], decode((ROOT/'deploy/probes.json').read_bytes()), state['expected'])
        else:
            require(not args.restart_sha and all(getattr(args, n) for n in
                    ('request','request_sha','objects','volume','probe','probe_sha')), 'EXACT_SESSION_ARGUMENTS_REQUIRED')
            root = isolated_volume(args.volume, os.environ.get('RUNNER_TEMP'))
            objects = LocalObjects(args.objects)
            raw = pinned_file(args.request, args.request_sha, 2*1024*1024)
            candidate = assemble(raw, args.request_sha, objects, root, ROOT)
            require(candidate['index']['review']['mode'] == 'isolated-acceptance'
                    and len(candidate['selected_regions']) == 1, 'ISOLATED_SINGLE_REGION_REQUIRED')
            composite = composite_for(candidate['index'], candidate['coverage'], objects)
            slug = candidate['selected_regions'][0]; row = composite.rows[slug]
            payload = probe_payload(pinned_file(args.probe, args.probe_sha, 8192), row['feature'], slug)
            checked_bucket(os.environ.get('R2_BUCKET'), raw, objects)
            import regional_r2
            counter = Counter(); client, bucket = regional_r2.connection()
            stack.callback(client.close)
            client.meta.events.register('before-send.s3.GetObject', counter.before_send)
            original = regional_r2.connection
            try:
                regional_r2.connection = lambda *_: (client, bucket)
                fetch = regional_r2.reader()
            finally:
                regional_r2.connection = original
            cache = ObjectCache(scratch/'cache', 512*1024*1024); stack.callback(cache.close)
            prefix = 'navigation/graphs/%s/%s/tiles/' % (slug, row['graph_fingerprint'])
            manifest = composite.objects.graphs[(slug, row['graph_fingerprint'])]
            bridge = Bridge(composite.objects, cache, counter.tile_reader(fetch, prefix, manifest['tiles'])).start()
            stack.callback(bridge.close)
            result = exercise(composite, root, native_engine(scratch/'native', stack, bridge), payload,
                              decode((ROOT/'deploy/probes.json').read_bytes()), counter, bridge)
            stat = root.stat()
            state = dict(index=candidate['index'], coverage=candidate['coverage'], objects=str(objects.root),
                         volume=str(root), volume_identity=[stat.st_dev,stat.st_ino], payload=payload, expected=result)
            run_restart(state, scratch, args.source_sha)
            result.update(schema=1, event='ISOLATED_NATIVE_RUNTIME_SESSION_PASSED', execution=context,
                          request_sha256=args.request_sha, probe_sha256=args.probe_sha,
                          catalog_sha256=candidate['index']['catalog_sha256'], restart_verified=True,
                          production_supply_complete=False, supply_catalog_complete=candidate['supply_catalog_complete'],
                          scope_routes_verified=False, published=False)
        write_private(args.output, canonical_bytes(result))
    print(json.dumps(dict(event='PRIVATE_NATIVE_RUNTIME_RESULT_WRITTEN',
                          sha256=hashlib.sha256(canonical_bytes(result)).hexdigest(),
                          production_activated=False)))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps(dict(event='ISOLATED_RUNTIME_GATE_FAILED', error=type(error).__name__)), file=sys.stderr)
        raise SystemExit(1) from None
