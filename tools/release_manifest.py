#!/usr/bin/env python3
"""One build/rescue READY gate: exact full roster, validated region reports,
immutable builder identity and every contiguous shard's size/SHA256 digest.
"""
import argparse
import hashlib
import json
import re
from pathlib import Path


def image(path):
    value = Path(path).read_text().strip()
    if not re.fullmatch(r'valhalla/valhalla@sha256:[0-9a-f]{64}', value):
        raise ValueError('builder must be pinned by digest')
    return value


def ready(release, roster, manifests, expected_image):
    wanted = {r['slug'] for r in roster['region']}
    found = {m['slug'] for m in manifests}
    if len(found) != len(manifests) or found != wanted:
        raise ValueError('exact production region roster required; diagnostic subsets cannot publish')
    assets = {a['name']: a for a in release['assets']}
    declared = {}
    tile_hashes = {}
    for m in manifests:
        if m['image'] != expected_image or m['validation'].get('validator') != 'gph-v3-index-v1' or m['validation']['tiles'] <= 0:
            raise ValueError('missing tile validation or mismatched builder image')
        inventory = m['validation'].get('tile_hashes', {})
        if len(inventory) != m['validation']['tiles']:
            raise ValueError('missing per-tile inventory')
        for path, digest in inventory.items():
            if path in tile_hashes and tile_hashes[path] != digest:
                raise ValueError('independent regional graphs overlap incompatibly: ' + path)
            tile_hashes[path] = digest
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
    return dict(schema=1, production=True, image=expected_image,
                regions=sorted(wanted), parts=[declared[n] for n in sorted(declared)])


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest='command', required=True)
    region = sub.add_parser('region')
    region.add_argument('slug'); region.add_argument('validation'); region.add_argument('parts', nargs='+')
    final = sub.add_parser('ready')
    final.add_argument('release'); final.add_argument('manifests', nargs='+')
    args = p.parse_args()
    pinned = image('deploy/valhalla-image.txt')
    if args.command == 'region':
        parts = []
        for name in sorted(args.parts):
            file = Path(name)
            digest = hashlib.sha256()
            with file.open('rb') as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b''):
                    digest.update(chunk)
            parts.append(dict(name=file.name, size=file.stat().st_size, sha256=digest.hexdigest()))
        result = dict(slug=args.slug, image=pinned,
                      validation=json.loads(Path(args.validation).read_text()), parts=parts)
    else:
        result = ready(json.loads(Path(args.release).read_text()),
                       json.loads(Path('deploy/regions.json').read_text()),
                       [json.loads(Path(f).read_text()) for f in args.manifests], pinned)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
