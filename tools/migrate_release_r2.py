#!/usr/bin/env python3
"""Migrate trusted released regional tiles to R2 without retaining whole graphs.

A 20-tile pilot receipt is mandatory before a full release migration. Only a
fully structurally validated and GET-verified region publishes its manifest.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'deploy'))
from regional_download import build_plans
from regional_release import canonical_hash, validate_supply
from migration_transport import connection
from regional_storage import atomic_json
from migration_r2 import Publisher
from migration_stream import inventory, migrate
from migration_contract import load_profile, source_contract, validate_profile_supply, migration_identity


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release-json', required=True)
    parser.add_argument('--ready', required=True)
    parser.add_argument('--contract', help='Registered cloud contract; requires exact --source-sha')
    parser.add_argument('--source-sha')
    parser.add_argument('--roster')
    parser.add_argument('--coverage')
    parser.add_argument('--image')
    parser.add_argument('--region', required=True, help='Exact slug, or all in full mode')
    parser.add_argument('--mode', choices=('pilot', 'full'), default='pilot')
    parser.add_argument('--work', required=True, help='Private ignored workspace; never repository source')
    parser.add_argument('--pilot-receipt', help='Required full-mode proof from a successful 20-tile pilot')
    args = parser.parse_args()
    work = Path(args.work).resolve(); work.mkdir(parents=True, exist_ok=True)
    release = json.loads(Path(args.release_json).read_text())
    ready_bytes = Path(args.ready).read_bytes()
    registered = None
    if args.contract:
        if any((args.roster, args.coverage, args.image)):
            parser.error('registered contract cannot override individual source inputs')
        profile = load_profile(args.contract)
        registered = source_contract(profile, args.source_sha)
        roster, coverage, image = (profile[key] for key in ('roster', 'coverage', 'image'))
        plans = validate_profile_supply(profile, release, ready_bytes)
    else:
        if args.source_sha:
            parser.error('--source-sha requires a registered --contract')
        roster = json.loads(Path(args.roster or ROOT/'deploy/regions.json').read_text())
        coverage = json.loads(Path(args.coverage or ROOT/'deploy/coverage.json').read_text())
        image = Path(args.image or ROOT/'deploy/valhalla-image.txt').read_text().strip()
        validate_supply(release, ready_bytes, roster, image, coverage)
        plans = build_plans(release, roster, image)
    ready = json.loads(ready_bytes) if ready_bytes != b'ok\n' else {}
    selected = plans if args.region == 'all' else [p for p in plans if p['slug'] == args.region]
    if not selected or args.mode == 'pilot' and len(selected) != 1:
        parser.error('pilot requires one exact region; full accepts one region or all')
    client, bucket = connection()
    publisher = Publisher(client, bucket)
    contract = migration_identity(release, image, coverage, plans, registered)
    if args.mode == 'full':
        if not args.pilot_receipt:
            parser.error('full migration requires --pilot-receipt')
        receipt = json.loads(Path(args.pilot_receipt).read_text())
        if (receipt.get('schema') != 1 or receipt.get('manifest_published') is not False
                or receipt.get('contract') != contract or receipt.get('bucket') != bucket or receipt.get('verified_tiles') != 20):
            raise ValueError('pilot receipt differs from release contract/bucket or did not verify 20 tiles')
    features = {f['properties']['slug']: f for f in coverage['features']}
    for plan in selected:
        slug = plan['slug']
        print(json.dumps(dict(event='region_inventory_started', region=slug)), flush=True)
        def progress(event):
            print(json.dumps(dict(region=slug, **publisher.metrics(), **event)), flush=True)
        tiles, headers = inventory(plan, work, progress=progress)
        graph = canonical_hash({path: item['sha256'] for path, item in tiles.items()})
        expected_graph = ready.get('region_manifests', {}).get(slug, {}).get('graph_fingerprint')
        if expected_graph is not None and graph != expected_graph:
            raise ValueError('validated source inventory differs from READY graph fingerprint')
        prefix = 'navigation/graphs/%s/%s/' % (slug, graph)
        def upload(relative, path, item):
            publisher.put(prefix + 'tiles/' + relative, path, item)
        before_upload, before_reuse = publisher.uploaded, publisher.reused
        result = migrate(plan, work, tiles, headers, upload, limit=20 if args.mode == 'pilot' else None, progress=progress)
        if args.mode == 'pilot':
            if result['tiles'] != 20:
                raise ValueError('pilot region has fewer than 20 validated tiles; choose a larger region')
            output = work / 'pilot-receipt.json'
            atomic_json(output, dict(schema=1, contract=contract, bucket=bucket, region=slug,
                graph_fingerprint=graph, verified_tiles=result['tiles'], manifest_published=False,
                **({'registered_source': registered} if registered is not None else {})))
            print(json.dumps(dict(event='pilot_verified', region=slug, tiles=20)), flush=True)
            continue
        manifest = dict(schema=1, slug=slug, image=image, graph_fingerprint=graph,
            coverage_sha256=canonical_hash(features[slug]), validation='gph-v3-index-v1',
            tiles={path: {k: item[k] for k in ('size', 'sha256')} for path, item in tiles.items()},
            validation_report=result, source=dict(kind='github-release-region', tag=release['tag_name'],
                parts=[{k: p[k] for k in ('name', 'size', 'sha256')} for p in plan['parts']]))
        provenance = ready.get('region_manifests', {}).get(slug, {}).get('input_provenance')
        if provenance:
            manifest['input_provenance'] = provenance
        native_proof = ready.get('region_manifests', {}).get(slug, {}).get('native_validation')
        if native_proof:
            manifest['native_validation'] = native_proof
        raw = json.dumps(manifest, sort_keys=True, separators=(',', ':')).encode()
        target = work / ('manifest-' + slug + '.json'); target.write_bytes(raw)
        item = dict(size=len(raw), sha256=hashlib.sha256(raw).hexdigest())
        publisher.put(prefix + 'manifests/' + item['sha256'] + '.json', target, item)
        atomic_json(work / ('receipt-' + slug + '.json'), dict(schema=1, contract=contract,
            slug=slug, graph_fingerprint=graph, manifest_sha256=item['sha256'], manifest_size=item['size'],
            feature=features[slug], tiles=result['tiles'], bucket=bucket,
            **({'registered_source': registered} if registered is not None else {})))
        print(json.dumps(dict(event='region_objects_verified', region=slug, tiles=result['tiles'],
            uploaded=publisher.uploaded-before_upload, reused=publisher.reused-before_reuse,
            manifest_sha256=item['sha256'])), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps(dict(event='tile_migration_failed', code=type(error).__name__)), file=sys.stderr)
        raise SystemExit(1) from None
