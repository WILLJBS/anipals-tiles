"""Explicit technical windows inside official source envelopes, never admin claims."""
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'deploy'))
from regional_catalog import contains
from regional_release import canonical_hash


def rectangle(bounds):
    west, south, east, north = bounds
    if not -180 <= west < east <= 180 or not -90 <= south < north <= 90:
        raise ValueError('invalid technical extraction rectangle')
    return dict(type='Polygon', coordinates=[[[west, south], [east, south],
                [east, north], [west, north], [west, south]]])


def intersects_rectangle(a, b, bounds):
    west, south, east, north = bounds
    dx, dy = b[0]-a[0], b[1]-a[1]
    low, high = 0.0, 1.0
    for p, q in ((-dx, a[0]-west), (dx, east-a[0]), (-dy, a[1]-south), (dy, north-a[1])):
        if p == 0:
            if q < 0:
                return False
        elif p < 0:
            low = max(low, q/p)
        else:
            high = min(high, q/p)
        if low > high:
            return False
    return True


def inside_source(bounds, geometry):
    ring = rectangle(bounds)['coordinates'][0]
    if not all(contains(geometry, point) for point in ring):
        raise ValueError('technical window extends beyond source envelope')
    # All corners are inside and no source boundary may intersect the window.
    # This also rejects an enclosed source hole; no approximate clipping occurs.
    west, south, east, north = bounds
    polygons = geometry['coordinates'] if geometry['type'] == 'MultiPolygon' else [geometry['coordinates']]
    for polygon in polygons:
        for source_ring in polygon:
            for a, b in zip(source_ring, source_ring[1:]):
                if intersects_rectangle(a, b, bounds):
                    raise ValueError('source boundary may cross technical window')


def create(index_bytes, gaps, evidence):
    index_sha = hashlib.sha256(index_bytes).hexdigest()
    if index_sha != gaps['source_sha256']['index']:
        raise ValueError('source index differs from audited gaps')
    features = {f['properties']['id']: f for f in json.loads(index_bytes)['features']}
    sources = {row['slug']: row for row in evidence}
    groups = [('central-america', 'central-america', None),
              ('port-aux-francais-window', 'australia-oceania', 'Port-aux-Français'),
              ('grytviken-window', 'south-america', 'Grytviken')]
    builds, polygons = [], []
    for slug, source_id, name in groups:
        probes = [p for p in gaps['custom_extract_required'] if p['name'] == name] if name else [
            p for p in gaps['custom_extract_required'] if 'central-america' in p['parent_candidates']]
        source = sources[source_id]; original = features[source_id]
        selected = original['geometry']
        bounds = None
        if name:
            lat, lon = probes[0]['lat'], probes[0]['lng']
            bounds = [lon-1.5, lat-1, lon+1.5, lat+1]
            inside_source(bounds, selected)
            selected = rectangle(bounds)
        for p in probes:
            if not contains(selected, (p['lng'], p['lat'])):
                raise ValueError('gap city outside prepared build coverage')
        snapshot = dict(url=source['url'], size=int(source['bytes']), md5=source['md5'].split()[0])
        row = dict(slug=slug, region=source_id, input_source=snapshot, bounds=bounds,
                   source_geometry=original['geometry'], probes=probes)
        builds.append(row)
        props = dict(slug=slug, region=source_id, source_id=source_id,
            pbf_url=original['properties']['urls']['pbf'], source_index_sha256=index_sha,
            input_source=snapshot, extraction='complete_ways-window' if bounds else 'identity',
            source_geometry_sha256=canonical_hash(original['geometry']))
        polygons.append(dict(type='Feature', properties=props, geometry=selected))
    if sum(len(row['probes']) for row in builds) != len(gaps['custom_extract_required']):
        raise ValueError('not every custom gap has exactly one build assignment')
    return dict(schema=1, source_index_sha256=index_sha, builds=builds), dict(
        type='FeatureCollection', schema=1, source='https://download.geofabrik.de/index-v1.json',
        source_index_sha256=index_sha, features=polygons)


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--index', required=True); parser.add_argument('--gaps', required=True)
    parser.add_argument('--source-evidence', required=True); parser.add_argument('--output-dir', required=True)
    args = parser.parse_args()
    spec, coverage = create(Path(args.index).read_bytes(), json.loads(Path(args.gaps).read_text()),
                            json.loads(Path(args.source_evidence).read_text()))
    output = Path(args.output_dir); output.mkdir(parents=True, exist_ok=True)
    head = {k: v for k, v in spec.items() if k != 'builds'}
    text = json.dumps(head, sort_keys=True)[:-1] + ', "builds": [\n'
    text += ',\n'.join(json.dumps(v, ensure_ascii=False, separators=(',', ':')) for v in spec['builds']) + '\n]}\n'
    (output/'global-gap-sources.json').write_text(text)
    (output/'global-gap-coverage.json').write_text(json.dumps(coverage, ensure_ascii=False, separators=(',', ':'))+'\n')
    roster = dict(region=[dict(slug=f['properties']['slug'], region=f['properties']['region'],
                               coverage_sha256=canonical_hash(f)) for f in coverage['features']])
    (output/'global-gap-regions.json').write_text(json.dumps(roster, indent=2)+'\n')
    print(json.dumps(dict(graphs=len(spec['builds']), covered_source_rows=sum(len(r['probes']) for r in spec['builds']))))


if __name__ == '__main__':
    main()
