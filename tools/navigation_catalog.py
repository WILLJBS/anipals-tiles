"""Offline complete-source catalog planner; not a runtime activation index."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from check_global_build import validate_inputs
from migration_contract import ROOT, load_profile, validate_profile_supply
from migration_receipts import verify_manifest, verify_receipt
from navigation_catalog_inputs import REVISION, checked_bytes, decode, exact, require
from regional_release import canonical_hash

REQUEST_SCHEMA = 'anipals-navigation-catalog-request-v1'
CATALOG_SCHEMA = 'anipals-navigation-catalog-v1'
LIMITS = dict(release=16*1024*1024, ready=4*1024*1024, receipt=8*1024*1024, manifest=32*1024*1024)


def registered_inputs(request, root):
    raw = (root/'tools/migration-contracts.json').read_bytes()
    require(hashlib.sha256(raw).hexdigest() == request['registry_sha256'], 'REVIEWED_REGISTRY_CHANGED')
    names = decode(raw)['contracts']
    profiles = {name: load_profile(name, root) for name in names}
    mapping_raw = (root/'deploy/global-additions-scopes.json').read_bytes()
    require(hashlib.sha256(mapping_raw).hexdigest() == request['scope_sha256'], 'REVIEWED_SCOPE_CHANGED')
    summary = validate_inputs(root/'deploy')
    mapping = decode(mapping_raw)['source_rows']
    slugs = [row['slug'] for p in profiles.values() for row in p['roster']['region']]
    require(len(slugs) == len(set(slugs)), 'REGISTERED_GRAPH_OWNERSHIP_OVERLAP')
    return profiles, mapping, summary


def sources(request, profiles, objects):
    entries, deferred = request['sources'], request['deferred_contracts']
    require(isinstance(entries, list) and entries and isinstance(deferred, list)
            and all(isinstance(v, str) for v in deferred)
            and len(set(deferred)) == len(deferred), 'EXPLICIT_SOURCE_SCOPE_REQUIRED')
    checked = {}
    for source in entries:
        exact(source, 'registry definition_sha256 allowed_runner_shas release ready', 'EXACT_SOURCE_REQUIRED')
        name = source['registry']
        require(isinstance(name, str) and name in profiles and name not in checked, 'UNKNOWN_OR_DUPLICATE_SOURCE')
        profile = profiles[name]
        require(source['definition_sha256'] == canonical_hash(profile['definition']), 'ORIGINAL_DEFINITION_CHANGED')
        allowed = source['allowed_runner_shas']
        require(isinstance(allowed, list) and 1 <= len(allowed) <= 32
                and all(isinstance(s, str) and REVISION.fullmatch(s) for s in allowed)
                and len(set(allowed)) == len(allowed), 'EXPLICIT_RUNNER_ALLOWLIST_REQUIRED')
        release = decode(objects.read(source['release'], LIMITS['release']))
        ready_raw = objects.read(source['ready'], LIMITS['ready'])
        plans = validate_profile_supply(profile, release, ready_raw)
        ready = {} if ready_raw == b'ok\n' else decode(ready_raw)
        checked[name] = dict(request=source, profile=profile, release=release, ready=ready, plans=plans)
    require(not set(checked) & set(deferred) and set(checked) | set(deferred) == set(profiles),
            'EVERY_REGISTERED_GROUP_MUST_BE_SELECTED_OR_EXPLICITLY_DEFERRED')
    require(len({v['profile']['image'] for v in checked.values()}) == 1, 'MIXED_NATIVE_ABI')
    return checked


def region(item, source, bucket, objects, source_rows):
    from migration_ci import receipt_key
    exact(item, 'registry slug runner_sha receipt manifest', 'EXACT_REGION_REFERENCE_REQUIRED')
    slug, runner = item['slug'], item['runner_sha']
    profile, release = source['profile'], source['release']
    require(isinstance(slug, str) and isinstance(runner, str)
            and runner in source['request']['allowed_runner_shas'], 'RUNNER_NOT_ALLOWLISTED')
    plan = next((p for p in source['plans'] if p['slug'] == slug), None)
    require(plan is not None, 'REGION_OUTSIDE_COMPLETE_SOURCE')
    ref = item['receipt']
    # Shape/hash validation precedes namespace interpolation.
    require(isinstance(ref, dict), 'RECEIPT_DESCRIPTOR_REQUIRED')
    key = receipt_key(runner, release['tag_name'], 'receipt-'+slug, ref.get('sha256', ''))
    receipt = decode(objects.read(ref, LIMITS['receipt'], key))
    manifest_ref = item['manifest']
    require(isinstance(manifest_ref, dict), 'MANIFEST_DESCRIPTOR_REQUIRED')
    args = SimpleNamespace(contract=item['registry'], migration_sha=runner, slug=slug,
                           tag=release['tag_name'], manifest_sha=manifest_ref.get('sha256'))
    feature = verify_receipt(receipt, args, profile, source['plans'], release, bucket)
    key = 'navigation/graphs/%s/%s/manifests/%s.json' % (slug, receipt['graph_fingerprint'], args.manifest_sha)
    raw = objects.read(manifest_ref, LIMITS['manifest'], key)
    expected = source['ready'].get('region_manifests', {}).get(slug)
    _, manifest = verify_manifest(raw, args, receipt, feature, profile, plan, expected)
    return dict(slug=slug, graph_fingerprint=receipt['graph_fingerprint'], feature=feature,
                image=profile['image'], tiles=receipt['tiles'], manifest=manifest_ref,
                receipt=ref, registered_source=receipt['registered_source'], contract=receipt['contract'],
                source_rows=source_rows,
                native_proof_sha256=canonical_hash(manifest['native_validation']) if 'native_validation' in manifest else None,
                input_provenance_sha256=canonical_hash(manifest['input_provenance']) if 'input_provenance' in manifest else None)


def plan(request_raw, request_sha, objects, root=ROOT):
    checked_bytes(request_raw, request_sha, len(request_raw), 2*1024*1024)
    request = decode(request_raw)
    exact(request, 'schema registry_sha256 scope_sha256 bucket sources deferred_contracts receipts', 'EXACT_CATALOG_REQUEST_REQUIRED')
    require(request['schema'] == REQUEST_SCHEMA and isinstance(request['bucket'], str)
            and 3 <= len(request['bucket']) <= 63, 'CATALOG_REQUEST_IDENTITY_INVALID')
    profiles, mapping, scope_summary = registered_inputs(request, Path(root))
    selected = sources(request, profiles, objects)
    expected = {p['slug'] for s in selected.values() for p in s['plans']}
    entries = request['receipts']
    require(isinstance(entries, list) and len(entries) == len(expected), 'COMPLETE_SELECTED_RECEIPT_ROSTER_REQUIRED')
    rows, seen = [], set()
    for item in entries:
        exact(item, 'registry slug runner_sha receipt manifest', 'EXACT_REGION_REFERENCE_REQUIRED')
        name, slug = item['registry'], item['slug']
        require(isinstance(name, str) and name in selected and isinstance(slug, str) and slug not in seen,
                'DUPLICATE_OR_UNSELECTED_GRAPH')
        rows.append(region(item, selected[name], request['bucket'], objects,
                           [r['source_row'] for r in mapping if r['scope'] == slug]))
        seen.add(slug)
    require(seen == expected, 'MISSING_OR_EXTRA_GRAPH')
    deferred = [dict(registry=name, slugs=sorted(r['slug'] for r in profiles[name]['roster']['region']))
                for name in sorted(request['deferred_contracts'])]
    return dict(schema=CATALOG_SCHEMA, request_sha256=request_sha,
                registry_sha256=request['registry_sha256'], scope_sha256=request['scope_sha256'],
                bucket=request['bucket'], image=next(iter(selected.values()))['profile']['image'],
                sources=[dict(s['request'], definition=s['profile']['definition']) for _, s in sorted(selected.items())],
                regions=sorted(rows, key=lambda r: r['slug']), deferred_contracts=deferred,
                counts=dict(expected_regions=sum(p['definition']['regions'] for p in profiles.values()),
                            catalog_regions=len(rows), deferred_regions=sum(len(d['slugs']) for d in deferred),
                            expected_source_scopes=scope_summary['source_rows'],
                            source_scopes_in_catalog=sum(len(r['source_rows']) for r in rows)),
                supply_complete=not deferred, validation_scope='receipt-manifest-source-metadata',
                tile_bytes_reverified=False, runtime_activated=False, scope_routes_verified=False,
                runtime_ownership_reviewed=False)
