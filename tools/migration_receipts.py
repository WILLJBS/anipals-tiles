"""One pure receipt/manifest verifier for native diagnostics and catalog planning.

The caller authenticates exact bytes and allowlists the original migration
revision; this helper never relabels it with the current diagnostic revision.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'deploy'))


def require(ok, code):
    if not ok:
        raise ValueError(code)


def verify_receipt(receipt, args, profile, plans, release, bucket):
    from migration_contract import migration_identity
    from regional_release import canonical_hash, sha
    require(isinstance(receipt, dict) and profile['name'] == args.contract, 'RECEIPT_PROFILE_DIFFERS')
    registered = dict(schema=1, registry=args.contract, checkout_sha=args.migration_sha,
                      definition_sha256=canonical_hash(profile['definition']))
    contract = migration_identity(release, profile['image'], profile['coverage'], plans, registered)
    require(type(receipt.get('schema')) is int and receipt['schema'] == 1 and receipt.get('bucket') == bucket
            and receipt.get('slug') == args.slug and receipt.get('contract') == contract
            and canonical_hash(receipt.get('registered_source')) == canonical_hash(registered)
            and sha(receipt.get('graph_fingerprint'))
            and sha(receipt.get('manifest_sha256')) and receipt['manifest_sha256'] == args.manifest_sha
            and type(receipt.get('manifest_size')) is int and 0 < receipt['manifest_size'] <= 32*1024*1024
            and type(receipt.get('tiles')) is int and receipt['tiles'] > 0,
            'INCOMPLETE_OR_SUBSTITUTED_REGION_RECEIPT')
    feature = next(f for f in profile['coverage']['features'] if f['properties']['slug'] == args.slug)
    require(canonical_hash(receipt.get('feature')) == canonical_hash(feature), 'RECEIPT_COVERAGE_DIFFERS')
    return feature


def verify_manifest(raw, args, receipt, feature, profile, plan, ready_region=None):
    from regional_objects import ObjectCatalog
    from regional_release import canonical_hash
    require(len(raw) == receipt['manifest_size'] and args.manifest_sha == receipt['manifest_sha256'],
            'MANIFEST_RECEIPT_BYTES_DIFFER')
    from navigation_catalog_inputs import decode
    decode(raw)  # Duplicate keys/nonfinite values cannot be masked by the runtime JSON reader.
    catalog = ObjectCatalog([(raw, args.manifest_sha)], profile['image'])
    identity = args.slug, receipt['graph_fingerprint']
    require(set(catalog.graphs) == {identity}, 'GRAPH_IDENTITY_DIFFERS')
    manifest = catalog.graphs[identity]
    require(len(manifest['tiles']) == receipt['tiles']
            and manifest['coverage_sha256'] == canonical_hash(feature)
            and canonical_hash(manifest.get('source')) == canonical_hash(dict(kind='github-release-region', tag=args.tag,
                parts=[{k: p[k] for k in ('name', 'size', 'sha256')} for p in plan['parts']])),
            'MANIFEST_SOURCE_OR_COVERAGE_DIFFERS')
    require(type(manifest.get('validation_report', {}).get('tiles')) is int
            and manifest['validation_report']['tiles'] == receipt['tiles'],
            'MANIFEST_FULL_VALIDATION_MISSING')
    if ready_region is not None:
        require(ready_region.get('graph_fingerprint') == receipt['graph_fingerprint']
                and ready_region.get('tiles') == receipt['tiles'], 'READY_GRAPH_DIFFERS')
        for field in ('input_provenance', 'native_validation'):
            require(canonical_hash(manifest.get(field)) == canonical_hash(ready_region.get(field)),
                    'MANIFEST_READY_PROOF_DIFFERS')
    from regional_release import validate_native_probes, validate_snapshot
    validate_native_probes(manifest, feature, args.slug)
    validate_snapshot(manifest, feature, args.slug)
    return catalog, manifest
