"""Pinned composite index adds immutable remote regions without replacing local ones."""
import hashlib
import json
import os
from pathlib import Path

from regional_catalog import contains, locations
from regional_gc import safe_path
from regional_download import load_helper

geometry = load_helper('regional_coverage', [Path(__file__).with_name('regional_coverage.py'),
    Path(__file__).parents[1] / 'tools/coverage.py']).geometry
from regional_objects import ObjectCatalog, SLUG
from regional_release import canonical_hash, sha
from regional_storage import atomic_json, sync_dir


def read_object(fetch, key, expected_sha, maximum, expected_size=None):
    chunks = iter(fetch(key))
    data = bytearray()
    try:
        for chunk in chunks:
            data.extend(chunk)
            if len(data) > maximum:
                raise ValueError('release object exceeds size limit')
    finally:
        close = getattr(chunks, 'close', None)
        if close:
            close()
    if (not sha(expected_sha) or hashlib.sha256(data).hexdigest() != expected_sha
            or expected_size is not None and len(data) != expected_size):
        raise ValueError('release object SHA256 or size mismatch')
    return bytes(data)


def cached_object(root, fetch, key, digest, maximum, size=None):
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    target = safe_path(root / digest)
    if target.exists():
        data = target.read_bytes()
        if len(data) <= maximum and hashlib.sha256(data).hexdigest() == digest and (size is None or size == len(data)):
            return data
        raise ValueError('cached release object is corrupt')
    data = read_object(fetch, key, digest, maximum, size)
    pending = safe_path(root / (digest + '.tmp'))
    with pending.open('wb') as stream:
        stream.write(data); stream.flush(); os.fsync(stream.fileno())
    os.replace(pending, target)
    sync_dir(root)
    return data


class Composite:
    def __init__(self, raw, digest, base_coverage, image, manifests):
        if not sha(digest) or hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError('composite index SHA256 mismatch')
        value = json.loads(raw)
        if (type(value.get('schema')) is not int or value['schema'] != 1
                or value.get('image') != image
                or value.get('local_coverage_sha256') != canonical_hash(base_coverage)):
            raise ValueError('composite image or local coverage mismatch')
        rows = value.get('remote_regions')
        if not isinstance(rows, list) or not 1 <= len(rows) <= 512:
            raise ValueError('empty composite remote roster')
        self.rows, self.digest, self.image = {}, digest, image
        features = list(base_coverage['features'])
        local = {f['properties']['slug'] for f in features}
        object_manifests = []
        for row in rows:
            slug, fp = row.get('slug'), row.get('graph_fingerprint')
            if not isinstance(slug, str) or not SLUG.fullmatch(slug) or slug in local or slug in self.rows or not sha(fp):
                raise ValueError('composite cannot replace or duplicate an existing region')
            feature = row['feature']
            if feature.get('type') != 'Feature' or feature['properties'].get('slug') != slug:
                raise ValueError('composite feature identity mismatch')
            geometry(feature['geometry'])
            probes = row.get('probes')
            if not isinstance(probes, list) or not 1 <= len(probes) <= 8:
                raise ValueError('remote graph requires bounded native activation probes')
            for probe in probes:
                points = locations(dict(locations=[dict(lat=probe['lat'], lon=probe['lng'])] * 2, costing='pedestrian'))
                if not contains(feature['geometry'], points[0]):
                    raise ValueError('activation probe outside declared coverage')
            raw_manifest = manifests[slug]
            expected = row['manifest_sha256']
            if type(row['manifest_size']) is not int or len(raw_manifest) != row['manifest_size']:
                raise ValueError('remote manifest size mismatch')
            checked = ObjectCatalog([(raw_manifest, expected)], image)
            manifest = checked.graphs.get((slug, fp))
            if manifest is None or manifest['coverage_sha256'] != canonical_hash(feature):
                raise ValueError('remote manifest graph or coverage mismatch')
            object_manifests.append((raw_manifest, expected))
            self.rows[slug] = row
            features.append(feature)
        self.objects = ObjectCatalog(object_manifests, image)
        self.coverage = dict(base_coverage, features=features)

    def prepare(self, root, slug):
        root = Path(root).resolve()
        row = self.rows[slug]; object_fp = row['graph_fingerprint']
        fp = canonical_hash(dict(manifest_sha256=row['manifest_sha256'], image=self.image))
        graph = safe_path(root / 'regions' / slug / fp)
        tiles = safe_path(graph / 'tiles')
        tiles.mkdir(parents=True, exist_ok=True)
        marker = safe_path(graph / '.complete.json')
        descriptor = dict(slug=slug, fingerprint=fp, storage='r2', release='r2-' + row['manifest_sha256'],
            object_fingerprint=object_fp,
            tile_dir=str(tiles.relative_to(root)), manifest_sha256=row['manifest_sha256'],
            tile_count=len(self.objects.graphs[(slug, object_fp)]['tiles']), probes=row['probes'])
        if marker.exists():
            if json.loads(marker.read_text()) != descriptor:
                raise ValueError('remote immutable graph marker changed')
        else:
            atomic_json(marker, descriptor)
        return descriptor


def load_composite(fetch, digest, base_coverage, image, cache_root):
    if not sha(digest):
        raise ValueError('exact composite index SHA256 required')
    raw = cached_object(cache_root, fetch, 'navigation/releases/' + digest + '/index.json', digest, 8 * 1024 * 1024)
    value = json.loads(raw)
    # Validate identities before turning any untrusted field into an object key.
    manifests = {}
    for row in value.get('remote_regions', []):
        slug, fp, digest = row.get('slug'), row.get('graph_fingerprint'), row.get('manifest_sha256')
        size = row.get('manifest_size')
        if (not isinstance(slug, str) or not SLUG.fullmatch(slug) or not sha(fp)
                or not sha(digest) or type(size) is not int or not 0 < size <= 32 * 1024 * 1024):
            raise ValueError('invalid remote manifest reference')
        key = 'navigation/graphs/%s/%s/manifest.json' % (slug, fp)
        manifests[slug] = cached_object(cache_root, fetch, key, digest, 32 * 1024 * 1024, size)
    return Composite(raw, hashlib.sha256(raw).hexdigest(), base_coverage, image, manifests)
