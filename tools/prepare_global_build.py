#!/usr/bin/env python3
"""Coalesce redundant central-America builds and freeze all source-row ownership."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
from coverage import build, digest
from global_coverage_plan import envelope, plan as compute_plan
from regional_catalog import contains


def city_record(city, position):
    return dict(source_row=position, name=city['name'], country=city.get('country'), lat=city['lat'], lng=city['lng'])


CORRECTION_KINDS = ('verified_target_relocation', 'scope_unavailable')


def load_corrections(corrections_bytes, cities):
    value = json.loads(corrections_bytes)
    if value.get('schema') != 1 or not isinstance(value.get('corrections'), list):
        raise ValueError('native scope corrections schema differs')
    applied = {}
    for correction in value['corrections']:
        row = correction.get('source_row'); kind = correction.get('kind')
        original, probe = correction.get('original'), correction.get('probe')
        if (type(row) is not int or not 0 <= row < len(cities) or row in applied
                or kind not in CORRECTION_KINDS or not isinstance(correction.get('slug'), str)):
            raise ValueError('invalid native scope correction entry')
        city = cities[row]
        if not isinstance(original, dict) or (original.get('lat'), original.get('lng')) != (city['lat'], city['lng']):
            raise ValueError('correction original coordinate differs from frozen source row ' + str(row))
        if kind == 'verified_target_relocation':
            if not isinstance(probe, dict):
                raise ValueError('relocation correction lacks a probe coordinate')
            lat, lng = probe.get('lat'), probe.get('lng')
            if type(lat) not in (int, float) or type(lng) not in (int, float) or not (-90 <= lat <= 90 and -180 <= lng <= 180):
                raise ValueError('relocation probe coordinate out of range')
        if not isinstance(correction.get('basis'), dict):
            raise ValueError('correction lacks source-bound basis evidence')
        applied[row] = correction
    return applied


def apply_corrections(probes, corrections, owners):
    for probe in probes:
        correction = corrections.get(probe['source_row'])
        if correction is None: continue
        if owners.get(probe['source_row'], ('', ''))[1] != correction['slug']:
            raise ValueError('correction slug differs from assigned graph scope')
        if correction['kind'] == 'verified_target_relocation':
            probe.update(lat=correction['probe']['lat'], lng=correction['probe']['lng'])
        probe['probe_correction'] = correction['kind']


def prepare(plan, index_bytes, cities_bytes, base_bytes, gap_spec, gap_coverage, corrections_bytes=b''):
    raw = dict(index=index_bytes, cities=cities_bytes, coverage=base_bytes)
    if {key: hashlib.sha256(value).hexdigest() for key, value in raw.items()} != plan['source_sha256']:
        raise ValueError('global build inputs differ from audited source hashes')
    cities = json.loads(cities_bytes)
    if isinstance(cities, dict): cities = cities['cities']
    base, index = json.loads(base_bytes), json.loads(index_bytes)
    reproduced = compute_plan(cities, base, index)
    if reproduced != {key: value for key, value in plan.items() if key != 'source_sha256'}:
        raise ValueError('global plan does not reproduce from pinned source bytes')
    corrections = load_corrections(corrections_bytes, cities) if corrections_bytes else {}
    gap_spec, gap_coverage = copy.deepcopy(gap_spec), copy.deepcopy(gap_coverage)
    gap_features = {f['properties']['slug']: f for f in gap_coverage['features']}
    source_features = {f['properties']['id']: f for f in index['features']}
    central = next(row for row in gap_spec['builds'] if row['slug'] == 'central-america')
    central_shape = gap_features['central-america']['geometry']
    originals = {row['source_row'] for row in plan['custom_extract_required']}
    owners = {}
    for row in gap_spec['builds']:
        row['probes'] = [city_record(cities[p['source_row']], p['source_row']) for p in row['probes'] if p['source_row'] in originals]
        for p in row['probes']:
            if p['source_row'] in owners: raise ValueError('duplicate original gap assignment')
            owners[p['source_row']] = ('gap', row['slug'])
    if set(owners) != originals:
        raise ValueError('gap inputs do not cover exactly the original residual rows')
    rows, scopes, coalesced = [], [], []
    for item in plan['new_extracts']:
        probes = [city_record(cities[p['source_row']], p['source_row']) for p in item['cities']]
        shared = source_features[item['source_id']]['properties'].get('parent') == 'central-america'
        if shared:
            if not all(contains(central_shape, (p['lng'], p['lat'])) for p in probes):
                raise ValueError('coalesced child city outside central-America graph')
            central['probes'].extend(probes)
            coalesced.append(dict(source_id=item['source_id'], source_rows=len(probes), shared_scope='central-america'))
            owner = ('gap', 'central-america')
        else:
            expected = 'https://download.geofabrik.de/' + item['region'] + '-latest.osm.pbf'
            if item['pbf_url'] != expected or item['slug'] != item['region'].replace('/', '-'):
                raise ValueError('build region identity differs from exact PBF source')
            rows.append({key: item[key] for key in ('slug', 'region')})
            scopes.append(dict(slug=item['slug'], probes=probes))
            owner = ('addition', item['slug'])
        for probe in probes:
            if probe['source_row'] in owners: raise ValueError('duplicate new graph assignment')
            owners[probe['source_row']] = owner
    if corrections:
        for builds in (gap_spec['builds'], scopes):
            for scope_row in builds: apply_corrections(scope_row['probes'], corrections, owners)
    for row in gap_spec['builds']:
        row['probes'].sort(key=lambda p: p['source_row'])
    source_rows = []
    installed = [(f, envelope(f)) for f in base['features']]
    for position, city in enumerate(cities):
        owner = owners.get(position)
        if owner is None:
            candidates = sorted(f['properties']['slug'] for f, b in installed
                if b[0] <= city['lng'] <= b[2] and b[1] <= city['lat'] <= b[3]
                and contains(f['geometry'], (city['lng'], city['lat'])))
            if not candidates: raise ValueError('source row has no graph build assignment')
            owner = ('local', candidates[0])
        source_rows.append(dict(city_record(city, position), delivery=owner[0], scope=owner[1]))
    mapping = dict(schema=1, source_sha256=plan['source_sha256'], source_rows=source_rows,
                   builds=scopes, coalesced=coalesced, acceptance='build-inputs-not-route-results',
                   attribution='City coordinates: GeoNames, CC BY 4.0; extract geometry: Geofabrik/OSM, ODbL.')
    roster = dict(region=rows, native_scopes_file='deploy/global-additions-scopes.json',
                  source_row_count=len(cities),
                  source_city_projection_sha256=digest([city_record(city,i) for i,city in enumerate(cities)]))
    coverage = build(index, roster, plan['source_sha256']['index'])
    if corrections:
        by_slug = {row['slug']: row['probes'] for row in scopes + gap_spec['builds']}
        for feature in coverage['features'] + gap_coverage['features']:
            props = feature['properties']
            for probe in by_slug[props['slug']]:
                if probe.get('probe_correction') != 'verified_target_relocation': continue
                if not contains(feature['geometry'], (probe['lng'], probe['lat'])):
                    raise ValueError('relocated probe outside its graph coverage: ' + props['slug'])
        mapping['corrections_sha256'] = hashlib.sha256(corrections_bytes).hexdigest()
        mapping['attribution'] += ' Probe corrections: reviewed source-bound overlays in deploy/global-scope-corrections.json.'
    def bind(roster_rows, features, builds):
        by_slug = {row['slug']: row['probes'] for row in builds}
        for feature in features:
            props = feature['properties']; probes = by_slug[props['slug']]
            props.update(native_probe_sha256=digest(probes), native_probe_count=len(probes),
                         native_source_rows=[p['source_row'] for p in probes])
            next(row for row in roster_rows if row['slug'] == props['slug'])['coverage_sha256'] = digest(feature)
    bind(rows, coverage['features'], scopes)
    gap_roster = dict(region=[{key: row[key] for key in ('slug', 'region')} for row in gap_spec['builds']])
    bind(gap_roster['region'], gap_coverage['features'], gap_spec['builds'])
    return roster, coverage, mapping, gap_spec, gap_coverage, gap_roster


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', required=True); parser.add_argument('--index', required=True)
    parser.add_argument('--cities', required=True); parser.add_argument('--base-coverage', default='deploy/coverage.json')
    parser.add_argument('--gap-spec', default='deploy/global-gap-sources.json')
    parser.add_argument('--gap-coverage', default='deploy/global-gap-coverage.json')
    parser.add_argument('--corrections', default='deploy/global-scope-corrections.json')
    parser.add_argument('--output-dir', required=True)
    args = parser.parse_args()
    corrections_bytes = Path(args.corrections).read_bytes() if args.corrections else b''
    values = prepare(json.loads(Path(args.plan).read_text()), Path(args.index).read_bytes(),
                     Path(args.cities).read_bytes(), Path(args.base_coverage).read_bytes(),
                     json.loads(Path(args.gap_spec).read_text()), json.loads(Path(args.gap_coverage).read_text()),
                     corrections_bytes)
    output = Path(args.output_dir); output.mkdir(parents=True, exist_ok=True)
    names = ('global-additions.json', 'global-additions-coverage.json', 'global-additions-scopes.json',
             'global-gap-sources.json', 'global-gap-coverage.json', 'global-gap-regions.json')
    # Mapping-byte SHA additionally protects the complete scope assignment table.
    encoded = [json.dumps(v, ensure_ascii=False, separators=(',', ':')) + '\n' for v in values]
    values[0]['scope_file_sha256'] = hashlib.sha256(encoded[2].encode()).hexdigest()
    encoded[0] = json.dumps(values[0], ensure_ascii=False, separators=(',', ':')) + '\n'
    for name, text in zip(names, encoded): (output/name).write_text(text)
    print(json.dumps(dict(additions=len(values[0]['region']), gap_graphs=len(values[3]['builds']),
        central_probes=len(next(row for row in values[3]['builds'] if row['slug']=='central-america')['probes']),
        source_rows=len(values[2]['source_rows']))))


if __name__ == '__main__':
    main()
