import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'deploy'))
from regional_catalog import Catalog
from regional_composite import Composite, load_composite, read_object
from regional_download import activate_region
from regional_release import canonical_hash
from test_regional_objects import IMAGE, inventory


def feature(slug, x):
    return dict(type='Feature', properties=dict(slug=slug), geometry=dict(type='Polygon',
        coordinates=[[[x, 0], [x+1, 0], [x+1, 1], [x, 1], [x, 0]]]))


def fixture():
    base = dict(type='FeatureCollection', features=[feature('local', 0)])
    shape = feature('remote', 2)
    manifest = inventory('remote', b'good')
    manifest['coverage_sha256'] = canonical_hash(shape)
    raw = json.dumps(manifest).encode()
    row = dict(slug='remote', graph_fingerprint=manifest['graph_fingerprint'], feature=shape,
               manifest_sha256=hashlib.sha256(raw).hexdigest(), manifest_size=len(raw),
               probes=[dict(lat=.5, lng=2.5)])
    index = dict(schema=1, image=IMAGE, local_coverage_sha256=canonical_hash(base), remote_regions=[row])
    return base, index, raw


def composite(base, index, manifest):
    raw = json.dumps(index).encode()
    return Composite(raw, hashlib.sha256(raw).hexdigest(), base, IMAGE, {'remote': manifest})


class CompositeTests(unittest.TestCase):
    def test_added_remote_graph_requires_verified_atomic_activation(self):
        base, index, manifest = fixture()
        value = composite(base, index, manifest)
        self.assertEqual(value.coverage['features'][0], base['features'][0])
        with tempfile.TemporaryDirectory() as name:
            root = Path(name).resolve()
            descriptor = value.prepare(root, 'remote')
            catalog = Catalog(root, value.coverage)
            self.assertEqual(catalog.available(), {})
            with self.assertRaises(ValueError):
                activate_region(root, descriptor, dict(verified=False))
            activate_region(root, descriptor, dict(verified=True, slug='remote', fingerprint=descriptor['fingerprint']))
            available = catalog.available()['remote']
            self.assertEqual(available['storage'], 'r2')
            self.assertEqual(available['object_fingerprint'], index['remote_regions'][0]['graph_fingerprint'])
            self.assertEqual(value.prepare(root, 'remote'), descriptor)
            # Index membership changes do not mutate an immutable regional marker.
            index['note'] = 'new composite release using the identical graph'
            self.assertEqual(composite(base, index, manifest).prepare(root, 'remote'), descriptor)

    def test_local_replacement_modified_coverage_and_missing_probes_fail(self):
        for change in ('replace', 'coverage', 'probes', 'base', 'digest'):
            base, index, manifest = fixture()
            row = index['remote_regions'][0]
            if change == 'replace': row['slug'] = 'local'
            if change == 'coverage': row['feature']['geometry']['coordinates'][0][1][0] = 4
            if change == 'probes': row['probes'] = []
            if change == 'base': index['local_coverage_sha256'] = '0' * 64
            if change == 'digest': row['manifest_sha256'] = '0' * 64
            with self.subTest(change=change), self.assertRaises(ValueError):
                composite(base, index, manifest)

    def test_remote_manifest_change_gets_distinct_lease_and_activation_identity(self):
        base, index, manifest = fixture()
        with tempfile.TemporaryDirectory() as name:
            root = Path(name).resolve()
            before = composite(base, index, manifest).prepare(root, 'remote')
            content = json.loads(manifest); content['provenance'] = 'new audited metadata'
            manifest = json.dumps(content).encode()
            index['remote_regions'][0].update(manifest_sha256=hashlib.sha256(manifest).hexdigest(), manifest_size=len(manifest))
            after = composite(base, index, manifest).prepare(root, 'remote')
            self.assertNotEqual(before['fingerprint'], after['fingerprint'])
            self.assertEqual(before['object_fingerprint'], after['object_fingerprint'])

    def test_immutable_cached_supply_survives_outage_and_corruption_fails_closed(self):
        base, index, manifest = fixture()
        raw = json.dumps(index).encode(); digest = hashlib.sha256(raw).hexdigest()
        def fetch(key):
            yield raw if key.endswith('/index.json') else manifest
        with tempfile.TemporaryDirectory() as root:
            loaded = load_composite(fetch, digest, base, IMAGE, root)
            self.assertEqual(set(loaded.rows), {'remote'})
            def forbidden(_):
                self.fail('immutable cached index made network request')
            self.assertEqual(load_composite(forbidden, digest, base, IMAGE, root).digest, digest)
            (Path(root) / digest).write_bytes(b'corrupt')
            with self.assertRaisesRegex(ValueError, 'corrupt'):
                load_composite(forbidden, digest, base, IMAGE, root)
        with self.assertRaisesRegex(ValueError, 'size limit'):
            read_object(lambda _: [b'too long'], 'key', '0'*64, 2)


if __name__ == '__main__':
    unittest.main()
