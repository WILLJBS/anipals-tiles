#!/usr/bin/env python3
"""Read-only native correlation evidence for one frozen source scope; no acceptance."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'deploy'))
from regional_catalog import contains
from regional_engine import Engine, EngineError
from regional_release import canonical_hash

from native_scope_records import OFFSETS, error_record, response_record


def diagnose(slug, tiles, probe, feature, validation, template, engine_factory=Engine, output_path=None):
    root = Path(tiles).resolve().parent
    region = dict(slug=slug, fingerprint=canonical_hash(validation['tile_hashes']),
                  tile_dir=str(Path(tiles).resolve()))
    marker = root/'.complete.json'
    if marker.exists():
        raise ValueError('diagnostic refuses to overwrite existing graph identity')
    # Exclusive creation cannot overwrite an identity created by another owner.
    with marker.open('x') as stream:
        stream.write(json.dumps(region))
    engine = None
    result = dict(schema=2, kind='native-scope-diagnostic-not-acceptance', slug=slug,
        source_row=probe['source_row'], source_sha256=canonical_hash(probe), coverage_sha256=canonical_hash(feature), graph_fingerprint=region['fingerprint'],
        points=[], routes=[], changes_to_source_or_acceptance=False)
    def request(action, payload):
        try:
            return response_record(action, engine.request(region, action, payload))
        except EngineError as error:
            return error_record(error)
    try:
        engine = engine_factory(template, root/'native-diagnostic-config')
        result['status'] = request('status', {})
        if result['status'].get('native_version') != '3.3.0':
            raise ValueError('diagnostic native ABI differs from pinned 3.3.0')
        origin = dict(lat=probe['lat'], lon=probe['lng'])
        points = [origin] + [dict(lat=probe['lat']+dy, lon=probe['lng']+dx) for dy, dx in OFFSETS]
        for index, point in enumerate(points):
            result['points'].append(dict(point_index=index,
                inside_coverage=contains(feature['geometry'], (point['lon'], point['lat'])),
                correlation=request('locate', dict(locations=[point], costing='pedestrian', verbose=True))))
        for index, target in enumerate(points[1:]):
            if not contains(feature['geometry'], (target['lon'], target['lat'])):
                result['routes'].append(dict(offset_index=index, inside_coverage=False, classification='outside_coverage'))
                continue
            outcome = request('route', dict(costing='pedestrian', locations=[origin,target]))
            result['routes'].append(dict(offset_index=index, inside_coverage=True, **outcome))
        return result
    except Exception as error:
        result['failure'] = error_record(error) if isinstance(error, EngineError) else dict(classification='execution_error')
        raise
    finally:
        try:
            if engine is not None:
                engine.close()
        finally:
            marker.unlink(missing_ok=True)
            Path(output_path or root/'native-scope-diagnostic.json').write_text(
                json.dumps(result, allow_nan=False)+'\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--slug', required=True); parser.add_argument('--source-row', required=True, type=int)
    parser.add_argument('--tiles', default='tiles'); parser.add_argument('--validation', default='tile-validation.json')
    parser.add_argument('--spec', default='deploy/global-additions-scopes.json')
    parser.add_argument('--coverage', default='deploy/global-additions-coverage.json')
    parser.add_argument('--output', default='native-scope-diagnostic.json')
    args = parser.parse_args()
    scope = next(x for x in json.loads(Path(args.spec).read_text())['builds'] if x['slug']==args.slug)
    probe = next(x for x in scope['probes'] if x['source_row']==args.source_row)
    feature = next(f for f in json.loads(Path(args.coverage).read_text())['features'] if f['properties']['slug']==args.slug)
    if canonical_hash(scope['probes']) != feature['properties']['native_probe_sha256']:
        raise ValueError('diagnostic scope differs from frozen coverage')
    validation = json.loads(Path(args.validation).read_text())
    template = json.loads(subprocess.check_output(['valhalla_build_config']))
    output = diagnose(args.slug, args.tiles, probe, feature, validation, template, output_path=args.output)
    print(json.dumps(dict(kind=output['kind'], classifications=[r['classification'] for r in output['routes']])))


if __name__ == '__main__':
    main()
