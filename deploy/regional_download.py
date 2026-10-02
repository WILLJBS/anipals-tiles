#!/usr/bin/env python3
"""Install independent fingerprinted regional graphs; never read RESET_TILES.
Only a durable completion marker exposes a graph. Native router verification is
required before authorizing removal of the incompatible legacy mixed graph.
"""
import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path
from regional_storage import atomic_json, digest, extract_parts, require_capacity, sync_dir
from regional_gc import activate, collect_retired


def load_helper(name, candidates):
    path = next((p for p in candidates if p.exists()), None)
    if path is None:
        raise FileNotFoundError(name)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_plans(release, roster, image):
    here = Path(__file__).parent
    planner = load_helper('release_plan', [here / 'release-plan.py', Path('/usr/local/bin/release-plan.py')])
    planner.plan(release, roster)  # exact roster, positive sizes, contiguous shards, SHA256
    if not re.fullmatch(r'valhalla/valhalla@sha256:[0-9a-f]{64}', image):
        raise ValueError('native image must be immutable')
    if not isinstance(release.get('tag_name'), str) or not release['tag_name']:
        raise ValueError('missing release identity')
    plans = []
    for region in roster['region']:
        slug = region['slug']
        if not re.fullmatch(r'[a-z0-9-]{1,120}', slug):
            raise ValueError('unsafe region slug')
        matches = [a for a in release['assets'] if re.fullmatch(r'tiles-' + re.escape(slug) + r'\.tar-\d{2,}', a['name'])]
        matches.sort(key=lambda a: int(a['name'].rsplit('-', 1)[1]))
        parts = [dict(name=a['name'], size=a['size'], sha256=a['digest'][7:], url=a['browser_download_url']) for a in matches]
        identity = dict(slug=slug, release=release['tag_name'], image=image,
                        parts=[{k: p[k] for k in ('name', 'size', 'sha256')} for p in parts])
        fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        plans.append(dict(identity, fingerprint=fingerprint, parts=parts))
    return plans


def download_part(path, part):
    local = Path(__file__).with_name('download-part.sh')
    helper = local if local.exists() else Path('/usr/local/bin/download-part.sh')
    subprocess.run(['sh', str(helper), str(path), part['url'], str(part['size']), part['sha256']], check=True)


def validate_tiles(path):
    here = Path(__file__).parent
    validator = load_helper('tile_validation', [here / 'validate_tiles.py', here.parent / 'tools/validate_tiles.py'])
    return validator.validate(path)


def cache_path(root, plan):
    return Path(root) / 'regions/.downloads' / plan['slug'] / plan['fingerprint']


def adopt_legacy_parts(root, plans):
    """Move cached bytes, not the mixed graph: no duplicate disk use before cleanup."""
    root = Path(root)
    for plan in plans:
        cache = cache_path(root, plan)
        for i, _ in enumerate(plan['parts']):
            old = root / 'tiles' / ('tiles-%s.part-%d' % (plan['slug'], i))
            target = cache / ('part-%02d' % i)
            if old.is_file() and not old.is_symlink() and not target.exists():
                cache.mkdir(parents=True, exist_ok=True)
                os.replace(old, target)
                sync_dir(cache)


def prepare_region(data_root, plan, downloader=None, validator=None, reserve_bytes=512 * 1024 * 1024):
    root = Path(data_root).resolve()
    slug, fp = plan['slug'], plan['fingerprint']
    if not re.fullmatch(r'[a-z0-9-]{1,120}', slug) or not re.fullmatch(r'[0-9a-f]{64}', fp):
        raise ValueError('unsafe region identity')
    parent = root / 'regions' / slug
    parent.mkdir(parents=True, exist_ok=True)
    final, stage = parent / fp, parent / ('.' + fp + '.staging')
    descriptor = dict(slug=slug, fingerprint=fp, tile_dir=str((final / 'tiles').relative_to(root)), release=plan['release'])
    # One owner per graph; lock spans download/extraction/publish and marker repair.
    with (parent / ('.' + fp + '.lock')).open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        cache = cache_path(root, plan)
        marker = final / '.complete.json'
        if marker.exists():
            complete = json.loads(marker.read_text())
            if any(complete.get(k) != v for k, v in descriptor.items()) or complete.get('tile_count', 0) <= 0 or not (final / 'tiles').is_dir():
                raise ValueError('completion marker differs from requested graph')
            if cache.exists():
                shutil.rmtree(cache)
                sync_dir(cache.parent)
            return complete
        # Stale unexposed extraction cannot consume the capacity needed to retry.
        if stage.exists():
            shutil.rmtree(stage)
        if final.exists():
            shutil.rmtree(final)
        cache.mkdir(parents=True, exist_ok=True)
        paths = [cache / ('part-%02d' % i) for i in range(len(plan['parts']))]
        total = sum(p['size'] for p in plan['parts'])
        remaining = sum(max(0, p['size'] - (f.stat().st_size if f.exists() else 0)) for p, f in zip(plan['parts'], paths))
        # Full part cache + extraction, never a third concatenated tar copy.
        require_capacity(root, remaining + total, reserve_bytes)
        for part, path in zip(plan['parts'], paths):
            (downloader or download_part)(path, part)
            if path.stat().st_size != part['size'] or digest(path) != part['sha256']:
                raise ValueError('download helper failed exact digest: ' + part['name'])
        shutil.rmtree(stage, ignore_errors=True)
        stage.mkdir()
        written = extract_parts(paths, stage / 'tiles', total)
        report = (validator or validate_tiles)(stage / 'tiles')
        if report.get('tiles', 0) <= 0:
            raise ValueError('no validated regional tiles')
        descriptor['tile_count'] = report['tiles']
        atomic_json(stage / '.identity.json', dict(fingerprint=fp, bytes=written, image=plan['image']))
        # A crash after rename but before marker only leaves an unavailable candidate;
        # rebuilding it from verified resumable parts on retry is safe and bounded.
        if final.exists():
            shutil.rmtree(final)
        os.replace(stage, final)
        sync_dir(parent)
        atomic_json(marker, descriptor)
        shutil.rmtree(cache)
        sync_dir(cache.parent)
        return descriptor


def verified(descriptor, result):
    return isinstance(result, dict) and result.get('verified') is True and all(result.get(k) == descriptor[k] for k in ('slug', 'fingerprint'))


def activate_region(data_root, descriptor, verification):
    if not verified(descriptor, verification):
        raise ValueError('native verification does not match candidate graph')
    activate(data_root, descriptor)


def cleanup_legacy(data_root, descriptor, health_callback):
    root = Path(data_root).resolve()
    regions = root / 'regions'
    regions.mkdir(parents=True, exist_ok=True)
    journal = regions / 'migration.json'
    with (regions / '.migration.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = json.loads(journal.read_text()) if journal.exists() else {'state': 'prepared'}
        if state['state'] == 'cleaned':
            return True
        if state['state'] == 'cleanup_authorized':
            # Resume only for the previously native-verified immutable graph.
            descriptor = state['descriptor']
        marker = root / 'regions' / descriptor['slug'] / descriptor['fingerprint'] / '.complete.json'
        if not marker.exists() or json.loads(marker.read_text()) != descriptor:
            return False
        # Even durable authorization must reacquire native health after restart.
        result = health_callback(descriptor)
        if not verified(descriptor, result):
            return False
        if state['state'] != 'cleanup_authorized':
            state = dict(state='cleanup_authorized', descriptor=descriptor)
            atomic_json(journal, state)  # authorization MUST survive power loss before deletion
        legacy, trash = root / 'tiles', regions / '.legacy-trash'
        if legacy.is_symlink() or trash.is_symlink():
            raise ValueError('refusing to delete symlinked legacy root')
        if legacy.exists():
            if trash.exists():
                shutil.rmtree(trash)
            os.replace(legacy, trash)
            sync_dir(root)
            sync_dir(regions)
        if trash.exists():
            shutil.rmtree(trash)
            sync_dir(regions)
        atomic_json(journal, dict(state='cleaned', descriptor=descriptor))
        return True


def verify_native(descriptor):
    request = urllib.request.Request('http://127.0.0.1:8002/verify',
        data=json.dumps({k: descriptor[k] for k in ('slug', 'fingerprint')}).encode(),
        headers={'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def priority(root, plan):
    cache = cache_path(root, plan)
    saved = sum(p.stat().st_size for p in cache.glob('part-*')) if cache.exists() else 0
    return (plan['slug'] != 'north-america-canada', -saved, sum(p['size'] for p in plan['parts']))


def run_pass(root, plans, health_callback=verify_native):
    adopt_legacy_parts(root, plans)
    failures = []
    for plan in sorted(plans, key=lambda p: priority(root, p)):
        try:
            collect_retired(root)
            descriptor = prepare_region(root, plan)
            result = health_callback(descriptor)
            activate_region(root, descriptor, result)
            collect_retired(root)
            cleanup_legacy(root, descriptor, lambda d: result if d == descriptor else health_callback(d))
            print(json.dumps({'region': plan['slug'], 'state': 'installed'}), flush=True)
        except Exception as error:
            failures.append(plan['slug'])
            print(json.dumps({'region': plan['slug'], 'error': str(error)}), flush=True)
    try:
        if collect_retired(root):
            failures.append('retired-graphs-awaiting-leases')
    except Exception as error:
        failures.append('retired-graph-collection')
        print(json.dumps({'event': 'retired_graph_collection_failed', 'error': str(error)}), flush=True)
    return failures


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--release-json', required=True)
    parser.add_argument('--data-root', default='/data')
    parser.add_argument('--registry', default='/usr/local/share/anipals-regions.json')
    parser.add_argument('--image-file', default='/usr/local/share/anipals-valhalla-image.txt')
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--retry-seconds', type=int, default=90)
    args = parser.parse_args()
    plans = build_plans(json.loads(Path(args.release_json).read_text()), json.loads(Path(args.registry).read_text()), Path(args.image_file).read_text().strip())
    while True:
        failed = run_pass(args.data_root, plans)
        if args.once or not failed:
            raise SystemExit(1 if failed else 0)
        time.sleep(max(1, args.retry_seconds))
