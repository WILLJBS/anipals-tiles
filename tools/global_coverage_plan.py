#!/usr/bin/env python3
"""Draft missing-extract builds from frozen cities and official polygons.

Aggregate-only islands are explicit unresolved clipping work, never silently
claimed as covered or scheduled as continent-sized builds.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'deploy'))
from regional_catalog import contains

AGGREGATES = {'asia', 'africa', 'europe', 'north-america', 'south-america',
              'central-america', 'australia-oceania', 'alps', 'britain-and-ireland'}


def envelope(feature):
    value = feature['geometry']
    polygons = [value['coordinates']] if value['type'] == 'Polygon' else value['coordinates']
    points = [p for polygon in polygons for ring in polygon for p in ring]
    return (min(p[0] for p in points), min(p[1] for p in points),
            max(p[0] for p in points), max(p[1] for p in points))


def plan(cities, current, index):
    shapes = [(f, envelope(f)) for f in index['features'] if f['properties'].get('urls', {}).get('pbf')]
    installed = [(f, envelope(f)) for f in current['features']]
    def matches(point, features):
        x, y = point
        return [f for f, b in features if b[0] <= x <= b[2] and b[1] <= y <= b[3] and contains(f['geometry'], point)]
    new, custom, missing, existing = {}, [], [], 0
    for position, city in enumerate(cities):
        point = (city['lng'], city['lat'])
        evidence = dict(source_row=position, name=city['name'], country=city.get('country'),
                        lat=point[1], lng=point[0])
        if matches(point, installed):
            existing += 1
            continue
        candidates = matches(point, shapes)
        if not candidates:
            missing.append(evidence)
            continue
        granular = [f for f in candidates if f['properties']['id'] not in AGGREGATES]
        if not granular:
            custom.append(dict(evidence, parent_candidates=sorted(f['properties']['id'] for f in candidates)))
            continue
        def score(feature):
            props = feature['properties']
            countries = props.get('iso3166-1:alpha2', [])
            b = envelope(feature)
            return (city.get('country') not in countries, -props['urls']['pbf'].count('/'),
                    (b[2]-b[0])*(b[3]-b[1]), props['id'])
        chosen = min(granular, key=score)['properties']
        url = chosen['urls']['pbf']
        region = url.removeprefix('https://download.geofabrik.de/').removesuffix('-latest.osm.pbf')
        record = new.setdefault(chosen['id'], dict(source_id=chosen['id'], region=region,
                       slug=region.replace('/', '-'), pbf_url=url, cities=[]))
        record['cities'].append(evidence)
    return dict(schema=1, source_rows=len(cities), existing_polygon_rows=existing,
                new_extracts=[new[k] for k in sorted(new)], custom_extract_required=custom,
                outside_all_official_polygons=missing, acceptance='polygon-planning-only')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cities', required=True)
    parser.add_argument('--index', required=True)
    parser.add_argument('--coverage', default='deploy/coverage.json')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    sources = {k: Path(getattr(args, k)).read_bytes() for k in ('cities', 'index', 'coverage')}
    cities = json.loads(sources['cities'])
    if isinstance(cities, dict):
        cities = cities['cities']
    value = plan(cities, json.loads(sources['coverage']), json.loads(sources['index']))
    value['source_sha256'] = {k: hashlib.sha256(v).hexdigest() for k, v in sources.items()}
    Path(args.output).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({k: len(v) if isinstance(v, list) else v for k, v in value.items()
                      if k not in ('source_sha256',)}))


if __name__ == '__main__':
    main()
