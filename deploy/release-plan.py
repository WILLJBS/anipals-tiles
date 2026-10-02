#!/usr/bin/env python3
"""Legacy READY compatibility requires the exact full roster and every part.
New releases additionally carry region validation manifests; production never
accepts a subset. Existing .complete disks bypass downloading in entrypoint.
"""
import json
import re
import sys
from pathlib import Path


def plan(release, regions):
    if release.get('draft') or release.get('prerelease'):
        raise ValueError('diagnostic/draft releases are not production supply')
    assets = {a['name']: a for a in release['assets']}
    if 'READY' not in assets:
        raise ValueError('missing READY')
    groups = {}
    for name, asset in assets.items():
        m = re.fullmatch(r'(tiles-[a-z0-9-]+)\.tar-(\d{2,})', name)
        if m:
            digest = asset.get('digest', '')
            if not re.fullmatch(r'sha256:[0-9a-f]{64}', digest) or asset['size'] <= 0:
                raise ValueError('missing/invalid asset SHA256 or size: ' + name)
            groups.setdefault(m[1], []).append((int(m[2]), asset))
    wanted = {'tiles-' + r['slug'] for r in regions['region']}
    if set(groups) != wanted:
        raise ValueError('release region roster differs from production roster')
    rows = []
    for slug in sorted(groups):
        parts = sorted(groups[slug])
        if [n for n, _ in parts] != list(range(len(parts))):
            raise ValueError('missing/duplicate part: ' + slug)
        for _, a in parts:
            rows.append((slug, a['browser_download_url'], str(a['size']), a['digest'][7:]))
    return rows


if __name__ == '__main__':
    try:
        rows = plan(json.load(sys.stdin), json.loads(Path(sys.argv[1]).read_text()))
        for row in rows:
            print('\t'.join(row))
    except (ValueError, KeyError) as error:
        sys.exit(str(error))
