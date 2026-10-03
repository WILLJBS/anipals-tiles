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


def verify(slug, tiles, probes, geometry, report_path):
    root = Path(tiles).resolve().parent
    report = json.loads(Path(report_path).read_text())
    region = dict(slug=slug, fingerprint=canonical_hash(report['tile_hashes']), tile_dir=str(Path(tiles).resolve()))
    marker = root / '.complete.json'
    if marker.exists():
        raise ValueError('native gate refuses to overwrite an existing graph marker')
    marker.write_text(json.dumps(region))
    engine = Engine(json.loads(subprocess.check_output(['valhalla_build_config'])), root / 'native-gate-config')
    output = []
    try:
        status = engine.request(region, 'status', {})
        if status.get('version') != '3.3.0':
            raise ValueError('unexpected native ABI version')
        for probe in probes:
            lat, lon = probe['lat'], probe['lng']
            for dy, dx in ((.002, .002), (.002, -.002), (-.002, .002), (-.002, -.002)):
                if not contains(geometry, (lon+dx, lat+dy)):
                    continue
                try:
                    result = engine.request(region, 'route', dict(costing='pedestrian',
                        locations=[dict(lat=lat, lon=lon), dict(lat=lat+dy, lon=lon+dx)]))
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
    finally:
        engine.close()
        marker.unlink(missing_ok=True)
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
    print(json.dumps(dict(slug=args.slug, verified_cities=len(result))))


if __name__ == '__main__':
    main()
