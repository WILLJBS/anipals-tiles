import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'deploy'))
from regional_release import canonical_hash, validate_snapshot


class GapProvenanceTests(unittest.TestCase):
    def test_fixed_sha_and_exact_clip_geometry_required(self):
        feature = json.loads((ROOT/'deploy/global-gap-coverage.json').read_text())['features'][1]
        props = feature['properties']; snapshot = props['input_source']
        proof = dict(source=dict(slug=props['slug'], source_url=snapshot['url'], size=snapshot['size'],
                                md5=snapshot['md5'], sha256='a'*64),
                     output=dict(size=100, sha256='b'*64), extraction='complete_ways-window',
                     clip_geometry_sha256=canonical_hash(feature['geometry']), way_references_checked=True)
        validate_snapshot({'input_provenance': proof}, feature, props['slug'])
        for fault in ('source-sha', 'source-date', 'clip', 'refs', 'output'):
            broken = copy.deepcopy(proof)
            if fault == 'source-sha': broken['source']['sha256'] = ''
            if fault == 'source-date': broken['source']['source_url'] = snapshot['url'].replace('261002', '261001')
            if fault == 'clip': broken['clip_geometry_sha256'] = '0'*64
            if fault == 'refs': broken['way_references_checked'] = False
            if fault == 'output': broken['output'] = {}
            with self.subTest(fault=fault), self.assertRaises(ValueError):
                validate_snapshot({'input_provenance': broken}, feature, props['slug'])
        with self.assertRaises(ValueError):
            validate_snapshot({}, feature, props['slug'])


if __name__ == '__main__':
    unittest.main()
