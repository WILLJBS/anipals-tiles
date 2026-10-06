#!/usr/bin/env python3
"""Actual pinned native routes for every declared gap-city coordinate."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'deploy'))
from regional_catalog import contains
from regional_engine import Engine, EngineError
from regional_release import canonical_hash
from native_scope_records import OFFSETS, error_record, response_record


def verify(slug, tiles, probes, geometry, report_path):
    root = Path(tiles).resolve().parent
    report = json.loads(Path(report_path).read_text())
    region = dict(slug=slug, fingerprint=canonical_hash(report['tile_hashes']), tile_dir=str(Path(tiles).resolve()))
    marker = root / '.complete.json'
    if marker.exists():
        raise ValueError('native gate refuses to overwrite an existing graph marker')
    marker.write_text(json.dumps(region))
    engine = None
    output = []
    diagnostic = dict(schema=2, kind='native-scope-diagnostic-not-acceptance', slug=slug,
        scope_sha256=canonical_hash(probes), graph_fingerprint=region['fingerprint'],
        changes_to_source_or_acceptance=False, routes=[])
    def request(action, payload, record):
        try:
            result = engine.request(region, action, payload)
        except EngineError as error:
            record.update(error_record(error))
            raise
        record.update(response_record(action, result))
        return result
    try:
        engine = Engine(json.loads(subprocess.check_output(['valhalla_build_config'])), root / 'native-gate-config')
        diagnostic['status'] = {}
        status = request('status', {}, diagnostic['status'])
        if status.get('version') != '3.3.0':
            raise ValueError('unexpected native ABI version')
        for probe in probes:
            if probe.get('probe_correction') == 'scope_unavailable':
                # Explicit reviewed unavailable scope: recorded, never routed, never verified.
                diagnostic['routes'].append(dict(source_row=probe['source_row'], offset_index=None,
                    inside_coverage=None, classification='scope_unavailable'))
                output.append(dict(source_row=probe['source_row'], name=probe['name'],
                    verified=False, classification='scope_unavailable'))
                continue
            lat, lon = probe['lat'], probe['lng']
            for index, (dy, dx) in enumerate(OFFSETS):
                inside = contains(geometry, (lon+dx, lat+dy))
                record = dict(source_row=probe['source_row'], offset_index=index, inside_coverage=inside)
                diagnostic['routes'].append(record)
                if not inside:
                    record['classification'] = 'outside_coverage'
                    continue
                try:
                    result = request('route', dict(costing='pedestrian',
                        locations=[dict(lat=lat, lon=lon), dict(lat=lat+dy, lon=lon+dx)]), record)
                except EngineError as error:
                    if error.status == 404:
                        continue
                    raise
                distance = result.get('trip', {}).get('summary', {}).get('length', 0)
                if 0 < distance < 5:
                    output.append(dict(source_row=probe['source_row'], name=probe['name'], distance_km=distance, verified=True))
                    break
            else:
                raise ValueError('no actual pedestrian route at declared gap city: ' + probe['name'])
    except Exception as error:
        diagnostic['failure'] = error_record(error) if isinstance(error, EngineError) else dict(classification='execution_error')
        raise
    finally:
        try:
            if engine is not None:
                engine.close()
        finally:
            marker.unlink(missing_ok=True)
            (root/'native-scope-diagnostic.json').write_text(json.dumps(diagnostic, allow_nan=False)+'\n')
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--slug', required=True); parser.add_argument('--tiles', default='tiles')
    parser.add_argument('--spec', default='deploy/global-gap-sources.json')
    parser.add_argument('--coverage', default='deploy/global-gap-coverage.json')
    parser.add_argument('--validation', default='tile-validation.json')
    parser.add_argument('--output', default='native-probes.json')
    args = parser.parse_args()
    row = next(r for r in json.loads(Path(args.spec).read_text())['builds'] if r['slug'] == args.slug)
    feature = next(f for f in json.loads(Path(args.coverage).read_text())['features'] if f['properties']['slug'] == args.slug)
    result = verify(args.slug, args.tiles, row['probes'], feature['geometry'], args.validation)
    Path(args.output).write_text(json.dumps(dict(slug=args.slug, native_version='3.3.0',
        scope_sha256=canonical_hash(row['probes']), probes=result), ensure_ascii=False) + '\n')
    print(json.dumps(dict(slug=args.slug, verified_cities=sum(1 for probe in result if probe.get('verified')),
        unavailable_scopes=sum(1 for probe in result if probe.get('classification') == 'scope_unavailable'))))


if __name__ == '__main__':
    main()
