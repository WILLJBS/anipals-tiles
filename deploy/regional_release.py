"""Validate READY content before trusting an isolated regional graph supply."""
import hashlib
import json
import re

LEGACY_TAG = 'tiles-20260929-26'


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def sha(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value) is not None


def parts(value):
    if not isinstance(value, list) or not value:
        raise ValueError('missing READY parts')
    result = {}
    for part in value:
        if not isinstance(part, dict) or set(part) != {'name', 'size', 'sha256'}:
            raise ValueError('invalid READY part declaration')
        name = part['name']
        if not isinstance(name, str) or name in result or type(part['size']) is not int or part['size'] <= 0 or not sha(part['sha256']):
            raise ValueError('duplicate or invalid READY part')
        result[name] = part
    return result


def validate_native_probes(manifest, feature, slug):
    props = feature['properties']
    if not props.get('native_probe_sha256'):
        return
    proof = manifest.get('native_validation', {})
    probes = proof.get('probes', [])
    if (proof.get('slug') != slug or proof.get('native_version') != '3.3.0'
            or proof.get('scope_sha256') != props['native_probe_sha256']
            or not isinstance(probes, list) or len(probes) != props['native_probe_count']):
        raise ValueError('native scope proof differs from frozen coverage contract')
    ids = set()
    for result in probes:
        row, length = result.get('source_row'), result.get('distance_km')
        if (type(row) is not int or row in ids or result.get('verified') is not True
                or type(length) not in (int, float) or not 0 < length < 5):
            raise ValueError('missing, duplicate or invalid native source-row route')
        ids.add(row)
    if ids != set(props['native_source_rows']):
        raise ValueError('native proof substituted source-row identities')


def validate_snapshot(manifest, feature, slug):
    """One publication/runtime gate for fixed PBF and technical-window provenance."""
    props = feature['properties']
    snapshot = props.get('input_source')
    if not snapshot:
        return
    proof = manifest.get('input_provenance', {})
    source, output = proof.get('source', {}), proof.get('output', {})
    if (source.get('slug') != slug or source.get('source_url') != snapshot['url']
            or source.get('size') != snapshot['size'] or source.get('md5') != snapshot['md5']
            or not sha(source.get('sha256')) or not sha(output.get('sha256'))
            or type(output.get('size')) is not int or output['size'] <= 0
            or proof.get('extraction') != props['extraction']):
        raise ValueError('missing pinned snapshot/extraction provenance')
    if proof['extraction'] == 'complete_ways-window' and (
            proof.get('clip_geometry_sha256') != canonical_hash(feature['geometry'])
            or proof.get('way_references_checked') is not True):
        raise ValueError('custom extraction geometry/reference gate missing')


def validate_supply(release, ready_bytes, roster, image, coverage):
    """Return None only for exact asset bytes and a supported supply contract.

    The single grandfathered marker never bypasses archive roster/hash checks.
    No other release may use the old three-byte READY instead of schema 2.
    """
    from regional_download import build_plans
    plans = build_plans(release, roster, image)
    if not isinstance(ready_bytes, bytes):
        raise ValueError('READY must be downloaded bytes')
    assets = release['assets']
    if len({a['name'] for a in assets}) != len(assets):
        raise ValueError('duplicate release asset names')
    marker = next((a for a in assets if a['name'] == 'READY'), None)
    if marker is None or type(marker.get('size')) is not int or marker['size'] != len(ready_bytes):
        raise ValueError('READY asset size mismatch')
    marker_digest = marker.get('digest')
    actual_digest = 'sha256:' + hashlib.sha256(ready_bytes).hexdigest()
    if marker_digest is not None and marker_digest != actual_digest:
        raise ValueError('READY asset digest mismatch')
    if release['tag_name'] == LEGACY_TAG and ready_bytes == b'ok\n':
        return
    if marker_digest != actual_digest:
        raise ValueError('schema 2 READY requires its asset SHA256')
    try:
        ready = json.loads(ready_bytes)
    except (ValueError, UnicodeError) as error:
        raise ValueError('invalid READY JSON') from error
    if not isinstance(ready, dict) or type(ready.get('schema')) is not int or ready['schema'] != 2 or ready.get('production') is not True:
        raise ValueError('unsupported READY schema or diagnostic supply')
    if ready.get('graph_layout') != 'isolated-regions-v1' or ready.get('image') != image:
        raise ValueError('READY layout/image mismatch')
    if ready.get('coverage_sha256') != canonical_hash(coverage):
        raise ValueError('READY coverage digest mismatch')
    expected = {row['slug']: row['region'] for row in roster['region']}
    coverage_locks = {row['slug']: row.get('coverage_sha256') for row in roster['region']}
    if ready.get('regions') != sorted(expected):
        raise ValueError('READY region roster mismatch')
    features = {f['properties']['slug']: f for f in coverage['features']}
    if set(features) != set(expected) or len(features) != len(coverage['features']):
        raise ValueError('coverage roster mismatch')
    regional = ready.get('region_manifests')
    if not isinstance(regional, dict) or set(regional) != set(expected):
        raise ValueError('READY regional manifest roster mismatch')
    all_parts = {}
    for plan in plans:
        slug = plan['slug']; manifest = regional[slug]; feature = features[slug]
        props = feature['properties']
        if coverage_locks.get(slug) and coverage_locks[slug] != canonical_hash(feature):
            raise ValueError('locked extraction coverage changed')
        validate_snapshot(manifest, feature, slug)
        validate_native_probes(manifest, feature, slug)
        if props.get('region') != expected[slug] or props.get('pbf_url') != 'https://download.geofabrik.de/' + expected[slug] + '-latest.osm.pbf':
            raise ValueError('coverage PBF/region mismatch')
        if not isinstance(manifest, dict) or not sha(manifest.get('graph_fingerprint')):
            raise ValueError('invalid regional graph fingerprint')
        if manifest.get('validation') != 'gph-v3-index-v1' or type(manifest.get('tiles')) is not int or manifest['tiles'] <= 0:
            raise ValueError('missing regional structural validation')
        if manifest.get('coverage_sha256') != canonical_hash(feature):
            raise ValueError('regional coverage digest mismatch')
        expected_parts = {p['name']: {k: p[k] for k in ('name', 'size', 'sha256')} for p in plan['parts']}
        if parts(manifest.get('parts')) != expected_parts:
            raise ValueError('regional READY parts differ from release assets')
        all_parts.update(expected_parts)
    if parts(ready.get('parts')) != all_parts:
        raise ValueError('global READY parts differ from regional assets')
