#!/usr/bin/env python3
"""Freeze exact Geofabrik extract polygons by their build PBF URL (no bbox guesses)."""
import argparse
import hashlib
import json
import math
import re
from pathlib import Path

SOURCE = 'https://download.geofabrik.de/index-v1.json'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def geometry(value):
    kind = value.get('type')
    if kind not in ('Polygon', 'MultiPolygon'):
        raise ValueError('coverage must be Polygon or MultiPolygon')
    polygons = [value['coordinates']] if kind == 'Polygon' else value['coordinates']
    if not polygons:
        raise ValueError('empty coverage')
    for polygon in polygons:
        if not polygon:
            raise ValueError('empty polygon')
        for ring in polygon:
            if len(ring) < 4 or ring[0] != ring[-1]:
                raise ValueError('coverage ring must be closed')
            for point in ring:
                if len(point) != 2 or any(isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n) for n in point):
                    raise ValueError('invalid coverage coordinate')
                if not -180 <= point[0] <= 180 or not -90 <= point[1] <= 90:
                    raise ValueError('coverage coordinate out of range')
    return value


def build(index, roster, source_sha256):
    if index.get('type') != 'FeatureCollection':
        raise ValueError('expected official GeoJSON FeatureCollection')
    by_url = {}
    for feature in index['features']:
        url = feature['properties'].get('urls', {}).get('pbf')
        if url in by_url:
            raise ValueError('duplicate PBF URL in source index')
        if url:
            by_url[url] = feature
    features = []
    for row in roster['region']:
        url = 'https://download.geofabrik.de/' + row['region'] + '-latest.osm.pbf'
        if url not in by_url:
            raise ValueError('no exact official extract polygon: ' + url)
        source = by_url[url]
        features.append(dict(type='Feature', properties=dict(slug=row['slug'], region=row['region'],
            source_id=source['properties']['id'], pbf_url=url, source_index_sha256=source_sha256),
            geometry=geometry(source['geometry'])))
    result = dict(type='FeatureCollection', schema=1, source=SOURCE,
                  source_index_sha256=source_sha256, features=features)
    validate(result, roster)
    return result


def validate(coverage, roster):
    expected = {r['slug']: r['region'] for r in roster['region']}
    features = coverage['features']
    locks = {r['slug']: r.get('coverage_sha256') for r in roster['region']}
    if coverage.get('source') != SOURCE or len(features) != len(expected):
        raise ValueError('exact official coverage roster required')
    seen = set()
    for feature in features:
        props = feature['properties']; slug = props['slug']
        if slug in seen or slug not in expected or props['region'] != expected[slug]:
            raise ValueError('duplicate or mismatched coverage region')
        if locks.get(slug) and locks[slug] != digest(feature):
            raise ValueError('locked extraction coverage changed; regenerate reviewed source inputs')
        if props['pbf_url'] != 'https://download.geofabrik.de/' + expected[slug] + '-latest.osm.pbf':
            raise ValueError('coverage PBF differs from build PBF')
        if props['source_index_sha256'] != coverage['source_index_sha256'] or not re.fullmatch(r'[0-9a-f]{64}', props['source_index_sha256']):
            raise ValueError('coverage source digest mismatch')
        snapshot = props.get('input_source')
        if snapshot:
            if (not isinstance(snapshot, dict) or type(snapshot.get('size')) is not int or snapshot['size'] <= 0
                    or not re.fullmatch('[0-9a-f]{32}', snapshot.get('md5', ''))
                    or not re.fullmatch(r'https://download\.geofabrik\.de/[a-z0-9/-]+-[0-9]{6}\.osm\.pbf', snapshot.get('url', ''))
                    or re.sub(r'-[0-9]{6}\.osm\.pbf$', '-latest.osm.pbf', snapshot['url']) != props['pbf_url']
                    or props.get('extraction') not in ('identity', 'complete_ways-window')
                    or not re.fullmatch('[0-9a-f]{64}', props.get('source_geometry_sha256', ''))):
                raise ValueError('invalid fixed snapshot/extraction declaration')
        if props.get('native_probe_sha256') and (not re.fullmatch('[0-9a-f]{64}', props['native_probe_sha256'])
                or type(props.get('native_probe_count')) is not int or props['native_probe_count'] <= 0
                or not isinstance(props.get('native_source_rows'), list)
                or len(props['native_source_rows']) != props['native_probe_count']
                or len(set(props['native_source_rows'])) != props['native_probe_count']
                or any(type(row) is not int or row < 0 for row in props['native_source_rows'])):
            raise ValueError('invalid native source-row scope contract')
        geometry(feature['geometry']); seen.add(slug)
    return {f['properties']['slug']: f for f in features}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--index', help='Downloaded official index; omitted to validate existing output')
    parser.add_argument('--roster', default='deploy/regions.json')
    parser.add_argument('--output', default='deploy/coverage.json')
    args = parser.parse_args()
    roster = json.loads(Path(args.roster).read_text())
    if args.index:
        data = Path(args.index).read_bytes()
        result = build(json.loads(data), roster, hashlib.sha256(data).hexdigest())
        # One complete feature per line; preserve all official coordinates without simplification.
        head = {k: v for k, v in result.items() if k != 'features'}
        text = json.dumps(head, sort_keys=True)[:-1] + ', "features": [\n'
        text += ',\n'.join(json.dumps(f, sort_keys=True, separators=(',', ':')) for f in result['features']) + '\n]}\n'
        Path(args.output).write_text(text)
    else:
        result = json.loads(Path(args.output).read_text())
        validate(result, roster)
    print(json.dumps(dict(regions=len(result['features']), coverage_sha256=digest(result))))


if __name__ == '__main__':
    main()
