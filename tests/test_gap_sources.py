import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / 'deploy'))
from gap_inputs import inside_source, rectangle
from gap_source import extract, hashes, pin
from regional_catalog import contains
from regional_release import canonical_hash


def row(body):
    return dict(slug='test-window', bounds=[1, 1, 2, 2], source_geometry=rectangle([0, 0, 3, 3]),
        input_source=dict(url='https://download.geofabrik.de/test-261002.osm.pbf',
                          size=len(body), md5=hashlib.md5(body).hexdigest()))


class GapSourcesTests(unittest.TestCase):
    def test_all_shared_and_residual_rows_have_exact_build_and_declared_coverage(self):
        spec = json.loads((ROOT / 'deploy/global-gap-sources.json').read_text())
        features = {f['properties']['slug']: f for f in json.loads((ROOT / 'deploy/global-gap-coverage.json').read_text())['features']}
        rows = []
        self.assertEqual(len(spec['builds']), 3)
        for build in spec['builds']:
            shape = features[build['slug']]
            self.assertEqual(shape['properties']['input_source'], build['input_source'])
            self.assertEqual(shape['properties']['source_geometry_sha256'], canonical_hash(build['source_geometry']))
            if build['bounds']:
                inside_source(build['bounds'], build['source_geometry'])
                self.assertEqual(shape['geometry'], rectangle(build['bounds']))
            else:
                self.assertEqual(shape['geometry'], build['source_geometry'])
            for probe in build['probes']:
                self.assertTrue(contains(shape['geometry'], (probe['lng'], probe['lat'])))
                rows.append(probe['source_row'])
        self.assertEqual(len(rows), 122)
        self.assertEqual(len(set(rows)), 122)
        central = next(row for row in spec['builds'] if row['slug'] == 'central-america')
        self.assertEqual(len(central['probes']), 120)

    def test_hidden_source_hole_and_boundary_crossing_are_rejected(self):
        source = rectangle([0, 0, 3, 3])
        source['coordinates'].append(rectangle([1.2, 1.2, 1.4, 1.4])['coordinates'][0])
        with self.assertRaises(ValueError): inside_source([1, 1, 2, 2], source)
        with self.assertRaises(ValueError): inside_source([2, 2, 4, 4], rectangle([0, 0, 3, 3]))
        inside_source([1, 1, 2, 2], rectangle([0, 0, 3, 3]))

    def test_source_pin_detects_provider_mismatch_and_post_pin_mutation(self):
        body = b'authenticated-source'; spec = row(body)
        with tempfile.TemporaryDirectory() as name:
            source = Path(name) / 'source.pbf'; source.write_bytes(body)
            lock = pin(spec, source)
            self.assertEqual(lock['sha256'], hashlib.sha256(body).hexdigest())
            source.write_bytes(b'changed-source')
            with self.assertRaises(ValueError): pin(spec, source)
            with self.assertRaises(ValueError):
                extract(spec, source, lock, Path(name) / 'output.pbf', runner=lambda *_args, **_kwargs: self.fail('untrusted source reached osmium'))

    @unittest.skipUnless(shutil.which('osmium'), 'real osmium fixture runs after apt installation in gap-build CI')
    def test_real_complete_ways_keeps_outside_nodes_and_check_refs_passes(self):
        xml = '''<osm version="0.6"><node id="1" lat="1.5" lon="1.5" version="1"/>
<node id="2" lat="1.5" lon="0.5" version="1"/><way id="1" version="1">
<nd ref="1"/><nd ref="2"/><tag k="highway" v="footway"/></way></osm>'''
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); (root/'source.osm').write_text(xml)
            source = root/'source.osm.pbf'
            subprocess.run(['osmium', 'cat', str(root/'source.osm'), '-o', str(source)], check=True)
            spec = row(source.read_bytes()); lock = pin(spec, source)
            proof, output = extract(spec, source, lock, root/'result.osm.pbf')
            text = subprocess.check_output(['osmium', 'cat', str(output), '-f', 'opl'], text=True)
            self.assertIn('n2 ', text)
            self.assertIn('Nn1,n2', text)
            self.assertTrue(proof['way_references_checked'])
            self.assertFalse(proof['complete_relations'])
            self.assertEqual(proof['output'], hashes(output))


if __name__ == '__main__':
    unittest.main()
