"""Assemble a private runtime candidate from reverified supply and observed rollback."""
import hashlib
from pathlib import Path
from navigation_catalog import plan as catalog_plan
from navigation_catalog_inputs import checked_bytes, decode, exact, require, SHA
from migration_contract import ROOT, load_profile, validate_profile_supply
from regional_release import canonical_hash
from regional_composite import Composite
from runtime_local_inventory import capture

REQUEST = 'anipals-runtime-index-request-v1'


def assemble(raw, request_sha, objects, volume_root, root=ROOT):
    checked_bytes(raw, request_sha, len(raw), 2*1024*1024)
    request = decode(raw)
    exact(request, 'schema catalog catalog_request mode selected_regions probes target_inventory target_identity_sha256 previous_index_sha256',
          'EXACT_RUNTIME_REQUEST_REQUIRED')
    require(request['schema'] == REQUEST and request['mode'] in ('production-candidate', 'isolated-acceptance'), 'RUNTIME_REQUEST_MODE')
    previous = request['previous_index_sha256']
    require(previous is None or isinstance(previous, str) and SHA.fullmatch(previous), 'PREVIOUS_INDEX_SHA_INVALID')
    catalog_raw = objects.read(request['catalog'], 32*1024*1024)
    catalog = decode(catalog_raw)
    source_raw = objects.read(request['catalog_request'], 2*1024*1024)
    recomputed = catalog_plan(source_raw, request['catalog_request']['sha256'], objects, root)
    require(canonical_bytes(catalog) == canonical_bytes(recomputed), 'CATALOG_DIFFERS_FROM_REVERIFIED_SOURCE_REQUEST')
    rows = {row['slug']: row for row in catalog['regions']}
    selected = request['selected_regions']
    require(isinstance(selected, list) and selected
            and all(isinstance(s, str) and s in rows for s in selected)
            and len(set(selected)) == len(selected), 'EXACT_SELECTED_RUNTIME_REGIONS_REQUIRED')
    selected = sorted(selected)
    production = request['mode'] == 'production-candidate'
    require(not production or catalog['supply_complete'] is True and not catalog['deferred_contracts']
            and catalog['counts']['expected_regions'] == 193 and len(rows) == 193
            and set(selected) == set(rows) and catalog['counts']['source_scopes_in_catalog'] == 6222,
            'PRODUCTION_REQUIRES_COMPLETE_193_SUPPLY_AND_SELECTION')
    baseline = load_profile('original61', Path(root))
    require(catalog['image'] == baseline['image'], 'RUNTIME_IMAGE_DIFFERS_FROM_BASELINE')
    baseline_features = {f['properties']['slug']: f for f in baseline['coverage']['features']}
    local_slugs = sorted(set(selected) & set(baseline_features))
    inventory_raw = objects.read(request['target_inventory'], 2*1024*1024)
    observed = decode(inventory_raw)
    current = capture(volume_root, selected, catalog['image'], request['target_identity_sha256'])
    require(canonical_bytes(observed) == canonical_bytes(current), 'TARGET_VOLUME_INVENTORY_CHANGED_OR_SUBSTITUTED')
    for state in observed['runtime_states'].values():
        if state is not None:
            require(state['value'].get('index_sha256') == previous, 'TARGET_PREVIOUS_INDEX_DIFFERS')
    local = {row['slug']: row for row in observed['regions']}
    source = next((s for s in catalog['sources'] if s['registry'] == 'original61'), None)
    require(not local_slugs or source is not None, 'LOCAL_SOURCE_PROVENANCE_MISSING')
    if local_slugs:
        release = decode(objects.read(source['release'], 16*1024*1024))
        ready = objects.read(source['ready'], 4*1024*1024)
        plans = {p['slug']: p for p in validate_profile_supply(baseline, release, ready)}
        for slug in local_slugs:
            require(local[slug]['fingerprint'] == plans[slug]['fingerprint']
                    and local[slug]['marker']['value']['release'] == plans[slug]['release'],
                    'OBSERVED_ROLLBACK_DIFFERS_FROM_VERIFIED_SOURCE')
    probes = request['probes']
    require(isinstance(probes, dict) and set(probes) == set(selected), 'EXACT_ACTIVATION_PROBE_ROSTER_REQUIRED')
    features = baseline['coverage']['features'] if production else [baseline_features[s] for s in local_slugs]
    coverage = dict(baseline['coverage'], features=features)
    remote, manifests, owners = [], {}, {}
    for slug in selected:
        row = rows[slug]; ref = row['manifest']
        require(slug not in baseline_features or canonical_hash(row['feature']) == canonical_hash(baseline_features[slug]),
                'BASELINE_FEATURE_CHANGED')
        remote.append(dict(slug=slug, graph_fingerprint=row['graph_fingerprint'], feature=row['feature'],
                           manifest_sha256=ref['sha256'], manifest_size=ref['bytes'], probes=probes[slug]))
        key = 'navigation/graphs/%s/%s/manifests/%s.json' % (slug, row['graph_fingerprint'], ref['sha256'])
        manifests[slug] = objects.read(ref, 32*1024*1024, key)
        if slug in local:
            tile_inventory = decode(manifests[slug])['tiles']
            require(local[slug]['tile_inventory_sha256'] == canonical_hash(tile_inventory)
                    and local[slug]['graph_fingerprint'] == row['graph_fingerprint'],
                    'ROLLBACK_TILE_SHA_DIFFERS_FROM_VERIFIED_MANIFEST')
        owners[slug] = dict(selected=dict(storage='r2', fingerprint=canonical_hash(dict(manifest_sha256=ref['sha256'],image=catalog['image']))),
                            rollback=dict(storage='local',fingerprint=local[slug]['fingerprint']) if slug in local else None)
    index = dict(schema=2, image=catalog['image'], local_coverage_sha256=canonical_hash(coverage),
                 catalog_sha256=request['catalog']['sha256'], previous_index_sha256=previous,
                 remote_regions=remote, storage_ownership=owners,
                 review=dict(request_sha256=request_sha, target_inventory_sha256=request['target_inventory']['sha256'],
                             target_identity_sha256=request['target_identity_sha256'], mode=request['mode'],
                             production_supply_complete=production, selected_regions=selected,
                             runtime_activated=False, scope_routes_verified=False))
    packed = canonical_bytes(index)
    Composite(packed, hashlib.sha256(packed).hexdigest(), coverage, catalog['image'], manifests)
    return dict(index=index, coverage=coverage, index_sha256=hashlib.sha256(packed).hexdigest(),
                production_supply_complete=production, supply_catalog_complete=catalog['supply_complete'],
                selected_regions=selected, runtime_activated=False, published=False,
                native_rollback_verified=False, local_rollback_sha_verified=True,
                tile_bytes_reverified=False, scope_routes_verified=False)


def canonical_bytes(value):
    import json
    return json.dumps(value,sort_keys=True,separators=(',', ':'),allow_nan=False).encode()
