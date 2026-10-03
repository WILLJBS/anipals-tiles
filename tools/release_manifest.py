#!/usr/bin/env python3
"""One build/rescue READY gate: exact full roster, validated region reports,
immutable builder identity and every contiguous shard's size/SHA256 digest.
"""
import argparse
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'deploy'))
from regional_release import validate_native_probes, validate_snapshot

_spec = importlib.util.spec_from_file_location("region_coverage", Path(__file__).with_name("coverage.py"))
COVERAGE = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(COVERAGE)


def image(path):
    value = Path(path).read_text().strip()
    if not re.fullmatch(r'valhalla/valhalla@sha256:[0-9a-f]{64}', value):
        raise ValueError('builder must be pinned by digest')
    return value


def ready(release, roster, manifests, expected_image, coverage):
    wanted = {r['slug'] for r in roster['region']}
    found = {m['slug'] for m in manifests}
    if len(found) != len(manifests) or found != wanted:
        raise ValueError('exact production region roster required; diagnostic subsets cannot publish')
    polygons = COVERAGE.validate(coverage, roster)
    assets = {a['name']: a for a in release['assets']}
    if len(assets) != len(release['assets']):
        raise ValueError('duplicate release asset names')
    declared = {}
    regional = {}
    for m in manifests:
        if m['image'] != expected_image or m['validation'].get('validator') != 'gph-v3-index-v1' or m['validation']['tiles'] <= 0:
            raise ValueError('missing tile validation or mismatched builder image')
        polygon = polygons[m['slug']]
        if m.get('coverage_sha256') != COVERAGE.digest(polygon) or m.get('pbf_url') != polygon['properties']['pbf_url']:
            raise ValueError('region manifest coverage/PBF provenance mismatch')
        snapshot = polygon['properties'].get('input_source')
        validate_snapshot(m, polygon, m['slug'])
        validate_native_probes(m, polygon, m['slug'])
        inventory = m['validation'].get('tile_hashes', {})
        if len(inventory) != m['validation']['tiles']:
            raise ValueError('missing per-tile inventory')
        for path, digest in inventory.items():
            if not re.fullmatch(r'[012]/(?:[0-9]{3}/)*[0-9]{3}\.gph', path) or not re.fullmatch(r'[0-9a-f]{64}', digest):
                raise ValueError('invalid regional tile inventory')
        # Independent roots intentionally may reuse paths with different GraphId indexes.
        # The fingerprint belongs to THIS region only, never a combined overlay.
        regional[m['slug']] = dict(graph_fingerprint=COVERAGE.digest(inventory),
            validation=m['validation']['validator'], tiles=m['validation']['tiles'],
            coverage_sha256=COVERAGE.digest(polygons[m['slug']]), parts=m['parts'])
        if snapshot:
            regional[m['slug']]['input_provenance'] = m['input_provenance']
        if polygon['properties'].get('native_probe_sha256'):
            regional[m['slug']]['native_validation'] = m['native_validation']
        parts = m['parts']
        if not parts or [p['name'] for p in parts] != ['tiles-%s.tar-%02d' % (m['slug'], i) for i in range(len(parts))]:
            raise ValueError('missing/duplicate shard sequence')
        for p in parts:
            a = assets.get(p['name'])
            if not a or p['size'] <= 0 or a['size'] != p['size'] or a.get('digest') != 'sha256:' + p['sha256']:
                raise ValueError('release shard size/SHA256 differs: ' + p['name'])
            declared[p['name']] = p
    actual = {n for n in assets if re.fullmatch(r'tiles-[a-z0-9-]+\.tar-\d{2,}', n)}
    if set(declared) != actual:
        raise ValueError('unexpected release shards')
    return dict(schema=2, production=True, graph_layout='isolated-regions-v1', image=expected_image,
                coverage_sha256=COVERAGE.digest(coverage), region_manifests=regional,
                regions=sorted(wanted), parts=[declared[n] for n in sorted(declared)])


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--roster', default='deploy/regions.json')
    p.add_argument('--coverage', default='deploy/coverage.json')
    p.add_argument('--image', default='deploy/valhalla-image.txt')
    sub = p.add_subparsers(dest='command', required=True)
    region = sub.add_parser('region')
    region.add_argument('--provenance'); region.add_argument('--native-validation'); region.add_argument('slug'); region.add_argument('validation'); region.add_argument('parts', nargs='+')
    final = sub.add_parser('ready')
    final.add_argument('release'); final.add_argument('manifests', nargs='+')
    args = p.parse_args()
    pinned = image(args.image)
    if args.command == 'region':
        parts = []
        for name in sorted(args.parts):
            file = Path(name)
            digest = hashlib.sha256()
            with file.open('rb') as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b''):
                    digest.update(chunk)
            parts.append(dict(name=file.name, size=file.stat().st_size, sha256=digest.hexdigest()))
        roster = json.loads(Path(args.roster).read_text())
        polygons = COVERAGE.validate(json.loads(Path(args.coverage).read_text()), roster)
        polygon = polygons[args.slug]
        result = dict(slug=args.slug, image=pinned, coverage_sha256=COVERAGE.digest(polygon),
                      pbf_url=polygon['properties']['pbf_url'],
                      validation=json.loads(Path(args.validation).read_text()), parts=parts)
        if args.native_validation:
            result['native_validation'] = json.loads(Path(args.native_validation).read_text())
        if args.provenance:
            result['input_provenance'] = json.loads(Path(args.provenance).read_text())
    else:
        result = ready(json.loads(Path(args.release).read_text()),
                       json.loads(Path(args.roster).read_text()),
                       [json.loads(Path(f).read_text()) for f in args.manifests], pinned,
                       json.loads(Path(args.coverage).read_text()))
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
