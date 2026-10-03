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

OFFSETS = ((.002, .002), (.002, -.002), (-.002, .002), (-.002, -.002))


def diagnose(slug, tiles, probe, feature, validation, template, engine_factory=Engine):
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
    result = dict(schema=1, kind='native-scope-diagnostic-not-acceptance', slug=slug,
        source=probe, coverage_sha256=canonical_hash(feature), graph_fingerprint=region['fingerprint'],
        points=[], routes=[], changes_to_source_or_acceptance=False)
    def request(action, payload):
        try:
            return dict(result=engine.request(region, action, payload))
        except EngineError as error:
            return dict(error_status=error.status, error=str(error))
    try:
        engine = engine_factory(template, root/'native-diagnostic-config')
        result['status'] = request('status', {})
        if result['status'].get('result', {}).get('version') != '3.3.0':
            raise ValueError('diagnostic native ABI differs from pinned 3.3.0')
        origin = dict(lat=probe['lat'], lon=probe['lng'])
        points = [origin] + [dict(lat=probe['lat']+dy, lon=probe['lng']+dx) for dy, dx in OFFSETS]
        for point in points:
            result['points'].append(dict(requested=point,
                inside_coverage=contains(feature['geometry'], (point['lon'], point['lat'])),
                correlation=request('locate', dict(locations=[point], costing='pedestrian', verbose=True))))
        for target in points[1:]:
            if not contains(feature['geometry'], (target['lon'], target['lat'])):
                result['routes'].append(dict(requested=[origin,target], classification='outside_coverage'))
                continue
            outcome = request('route', dict(costing='pedestrian', locations=[origin,target]))
            length = outcome.get('result', {}).get('trip', {}).get('summary', {}).get('length')
            category = ('native_error' if 'error_status' in outcome else 'missing_length' if length is None
                        else 'zero_length' if length == 0 else 'nonzero_route')
            result['routes'].append(dict(requested=[origin,target], classification=category, **outcome))
        return result
    finally:
        try:
            if engine is not None:
                engine.close()
        finally:
            marker.unlink(missing_ok=True)


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
    output = diagnose(args.slug, args.tiles, probe, feature, validation, template)
    Path(args.output).write_text(json.dumps(output, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(dict(kind=output['kind'], classifications=[r['classification'] for r in output['routes']])))


if __name__ == '__main__':
    main()
