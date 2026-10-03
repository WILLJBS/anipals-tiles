#!/usr/bin/env python3
"""Pin actual authenticated snapshot bytes, then create a declared technical extract."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

from gap_inputs import inside_source, rectangle


def hashes(path):
    sha, md5, size = hashlib.sha256(), hashlib.md5(), 0
    with Path(path).open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            sha.update(chunk); md5.update(chunk); size += len(chunk)
    return dict(sha256=sha.hexdigest(), md5=md5.hexdigest(), size=size)


def pin(row, source):
    expected = row['input_source']
    if not re.fullmatch(r'https://download\.geofabrik\.de/[a-z0-9/-]+-[0-9]{6}\.osm\.pbf', expected['url']):
        raise ValueError('dated official PBF source required')
    actual = hashes(source)
    if actual['size'] != expected['size'] or actual['md5'] != expected['md5']:
        raise ValueError('source differs from authenticated official snapshot size/MD5')
    return dict(schema=1, slug=row['slug'], source_url=expected['url'], **actual)


def extract(row, source, lock, output, runner=subprocess.run):
    # A stored SHA is mandatory and rechecked before osmium reads source bytes.
    if not re.fullmatch('[0-9a-f]{64}', lock.get('sha256', '')) or lock != pin(row, source):
        raise ValueError('source changed after SHA256 pinning')
    output = Path(output)
    if output.exists():
        raise ValueError('refusing to overwrite an extraction output')
    if row['bounds'] is None:
        # Identity input needs no duplicate 790 MB copy. Caller filters source.
        return dict(source=lock, extraction='identity', output=hashes(source), osmium=None), Path(source)
    inside_source(row['bounds'], row['source_geometry'])
    geometry = rectangle(row['bounds'])
    polygon = output.with_suffix('.geojson')
    polygon.write_text(json.dumps(dict(type='Feature', properties={}, geometry=geometry)))
    runner(['osmium', 'extract', '-p', str(polygon), '-s', 'complete_ways',
            '-o', str(output), str(source)], check=True, stdout=sys.stderr)
    # Default check-refs verifies way→node completeness. complete_ways makes no
    # promise of complete relations; we deliberately do not claim that here.
    runner(['osmium', 'check-refs', str(output)], check=True, stdout=sys.stderr)
    version = runner(['osmium', '--version'], check=True, capture_output=True, text=True).stdout.splitlines()[0]
    return dict(source=lock, extraction='complete_ways-window', output=hashes(output),
        clip_geometry_sha256=hashlib.sha256(json.dumps(geometry, sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
        osmium=version, way_references_checked=True, complete_relations=False), output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('pin', 'extract'))
    parser.add_argument('--spec', default='deploy/global-gap-sources.json')
    parser.add_argument('--slug', required=True); parser.add_argument('--pbf', required=True)
    parser.add_argument('--lock', required=True); parser.add_argument('--output')
    parser.add_argument('--provenance')
    args = parser.parse_args()
    rows = json.loads(Path(args.spec).read_text())['builds']
    row = next(r for r in rows if r['slug'] == args.slug)
    if args.action == 'pin':
        lock = pin(row, args.pbf)
        Path(args.lock).write_text(json.dumps(lock, sort_keys=True) + '\n')
        print(json.dumps(dict(event='source_sha256_pinned', slug=args.slug, sha256=lock['sha256'])))
    else:
        if not args.output or not args.provenance:
            parser.error('extract requires --output and --provenance')
        proof, selected = extract(row, args.pbf, json.loads(Path(args.lock).read_text()), args.output)
        Path(args.provenance).write_text(json.dumps(proof, sort_keys=True) + '\n')
        print(str(selected))


if __name__ == '__main__':
    main()
