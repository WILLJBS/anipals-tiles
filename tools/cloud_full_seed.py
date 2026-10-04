"""Restore original full-scope file/range identities without crediting partial caches as files."""
import hashlib
import json


def install_seed(journal, seed, regions, index, con, registry, persist=True):
    from expand_cities import overlap
    if len(regions) != 6222 or seed.get('sourceScopeCount') != 6222:
        raise ValueError('FULL_ROSTER_REQUIRED')
    expected = {}
    for theme, kind in [('base', 'land_use'), ('places', 'place')]:
        rows = con.execute('select bbox,assets.aws.href,assets.aws."file:size" from read_parquet(?) where assets.aws.href like ?',
                           [str(index), f'%/theme={theme}/type={kind}/%']).fetchall()
        for box, url, size in rows:
            match = {c: b for c, b in regions.items() if overlap(box, b)}
            if not match: continue
            identity = {'url': url, 'size': size, 'regions': match, 'policy': registry['version'],
                        'namedOnly': True, 'geometryMode': 'deferred'}
            if theme == 'places': identity['placesProjection'] = 'zoo-prefilter-v1'
            config = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:16]
            expected[hashlib.sha256(url.encode()).hexdigest()[:20]+'-'+config+'.json'] = url
    if len(expected) != seed['sourceFilesTotal']: raise ValueError('FULL_SOURCE_INDEX_DIFFERS')
    files = {}
    for item in seed['completedSourceFiles']:
        if (item['name'] in files or expected.get(item['name']) != item['sourceUrl']
                or item['configSha'] != item['name'][21:37]):
            raise ValueError('SEED_FILE_CONFIG_DIFFERS')
        files[item['name']] = item['blob']
    ranges = {}
    for item in seed['ranges']:
        start, end = item['meta']['range']; url = item['meta']['url']; key = item['cacheKey']
        if (key in ranges or key != hashlib.sha256(f'{url}:{start}:{end}'.encode()).hexdigest()
                or start < 0 or not 0 < end-start+1 <= 20_000_000
                or item['blob']['bytes'] != end-start+1 or item['meta']['sha256'] != item['blob']['sha256']):
            raise ValueError('SEED_RANGE_IDENTITY_DIFFERS')
        ranges[key] = {k: item[k] for k in ('cacheKey', 'meta', 'blob')}
    if journal.seq or journal.files or journal.ranges:
        if any(journal.files.get(k) != v for k, v in files.items()) or any(journal.ranges.get(k) != v for k, v in ranges.items()):
            raise ValueError('GLOBAL_SEED_STATE_DRIFT')
        return
    journal.files.update(files); journal.ranges.update(ranges)
    if persist: journal.snapshot()
