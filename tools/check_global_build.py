#!/usr/bin/env python3
"""Offline CI proof of full source-row ownership, scope hashes and probe contracts."""
import argparse
import hashlib
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'deploy'))
from coverage import digest, validate
from regional_catalog import contains, locations

FIELDS = ('source_row', 'name', 'country', 'lat', 'lng')


def validate_inputs(root):
    root = Path(root)
    read = lambda name: json.loads((root/name).read_text())
    roster = read('global-additions.json'); coverage = read('global-additions-coverage.json')
    mapping_bytes = (root/'global-additions-scopes.json').read_bytes()
    mapping = json.loads(mapping_bytes)
    if hashlib.sha256(mapping_bytes).hexdigest() != roster['scope_file_sha256']:
        raise ValueError('source-scope mapping byte SHA256 mismatch')
    base_bytes = (root/'coverage.json').read_bytes()
    if hashlib.sha256(base_bytes).hexdigest() != mapping['source_sha256']['coverage']:
        raise ValueError('old local coverage source changed')
    gaps, gap_coverage, gap_roster = read('global-gap-sources.json'), read('global-gap-coverage.json'), read('global-gap-regions.json')
    validate(coverage, roster); validate(gap_coverage, gap_roster)
    all_features = {
        'local': {f['properties']['slug']: f for f in json.loads(base_bytes)['features']},
        'addition': {f['properties']['slug']: f for f in coverage['features']},
        'gap': {f['properties']['slug']: f for f in gap_coverage['features']}}
    if any(set(all_features[a]) & set(all_features[b]) for a,b in [('local','addition'),('local','gap'),('addition','gap')]):
        raise ValueError('graph ownership overlaps across build sources')
    rows = mapping['source_rows']
    if len(rows) != roster['source_row_count'] or digest([{key: r[key] for key in FIELDS} for r in rows]) != roster['source_city_projection_sha256']:
        raise ValueError('source-city projection changed from generated inputs')
    if [r['source_row'] for r in rows] != list(range(len(rows))):
        raise ValueError('source rows missing, reordered or duplicated')
    grouped = {}
    for row in rows:
        shape = all_features[row['delivery']][row['scope']]['geometry']
        points = locations(dict(costing='pedestrian', locations=[dict(lat=row['lat'], lon=row['lng'])]*2))
        if not contains(shape, points[0]): raise ValueError('source coordinate outside assigned graph')
        grouped.setdefault((row['delivery'],row['scope']), []).append({key: row[key] for key in FIELDS})
    for delivery, builds in [('addition', mapping['builds']), ('gap', gaps['builds'])]:
        if {row['slug'] for row in builds} != set(all_features[delivery]):
            raise ValueError('native scope registry differs from graph roster')
        for scope in builds:
            probes = scope['probes']; feature = all_features[delivery][scope['slug']]
            if probes != grouped.get((delivery,scope['slug'])):
                raise ValueError('native probes omit or alter assigned source rows')
            props = feature['properties']
            if (props['native_probe_sha256'] != digest(probes) or props['native_probe_count'] != len(probes)
                    or props['native_source_rows'] != [p['source_row'] for p in probes]):
                raise ValueError('native probe hash/count differs from frozen coverage')
    return dict(source_rows=len(rows), local_rows=sum(r['delivery']=='local' for r in rows),
                addition_rows=sum(r['delivery']=='addition' for r in rows),
                gap_rows=sum(r['delivery']=='gap' for r in rows),
                additions=len(roster['region']), gap_graphs=len(gaps['builds']),
                coalesced_children=len(mapping['coalesced']))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default='deploy')
    args = parser.parse_args()
    print(json.dumps(validate_inputs(args.root)))
