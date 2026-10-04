"""Offline controls for the protocol gate that CI also invokes on real Canada."""
import ast
import copy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tests'))
from native_locate_smoke import locate_smoke


class NativeLocateProtocol(unittest.TestCase):
    def engine(self, change=lambda r:r):
        class Fake:
            def __init__(self): self.calls = []
            def request(self, region, action, payload):
                self.calls.append((action, copy.deepcopy(payload)))
                return change([dict(input_lat=43.7064, input_lon=-79.3986,
                    edges=[dict(correlated_lat=43.7065,correlated_lon=-79.3986)])])
        return Fake()

    def test_both_real_protocol_shapes_are_consumed_by_shared_parser(self):
        engine = self.engine()
        reports = locate_smoke(engine, {}, dict(lat=43.7064,lon=-79.3986))
        self.assertEqual([p['verbose'] for _,p in engine.calls], [False, True])
        self.assertTrue(all(action == 'locate' for action,_ in engine.calls))
        self.assertEqual([r['valid_projection_count'] for r in reports], [1,1])
        self.assertTrue(all(11 < r['nearest_projection_m'] < 12 for r in reports))

    def test_wrong_legacy_field_or_missing_coordinate_cannot_pass_gate(self):
        for edges in ([],[dict(projected=dict(lat=43.7065,lon=-79.3986))],
                      [dict(correlated_lat=43.7065)],
                      [dict(correlated_lat=True,correlated_lon=-79.3986)]):
            def change(response): response[0]['edges']=edges; return response
            with self.assertRaises(AssertionError):
                locate_smoke(self.engine(change),{},dict(lat=43.7064,lon=-79.3986))
        def wrong_input(response): response[0]['input_lat']=0; return response
        with self.assertRaisesRegex(AssertionError, 'LOCATE_INPUT_LAT'):
            locate_smoke(self.engine(wrong_input),{},dict(lat=43.7064,lon=-79.3986))

    def test_actual_native_gate_and_image_both_include_shared_protocol_check(self):
        tree=ast.parse((ROOT/'tests/native_smoke.py').read_text())
        calls=[node for node in ast.walk(tree) if isinstance(node,ast.Call)
               and isinstance(node.func,ast.Name) and node.func.id=='locate_smoke']
        self.assertEqual(len(calls),1)
        self.assertEqual([arg.id for arg in calls[0].args[:2]],['engine','region'])
        docker=(ROOT/'deploy/Dockerfile').read_text()
        self.assertIn('COPY tests/native_locate_smoke.py tests/native_scope_geometry.py /usr/local/lib/anipals/',docker)
        for workflow in ('deploy-image.yml','remote-tiles-diagnostic.yml'):
            self.assertIn('/usr/local/lib/anipals/native_smoke.py', (ROOT/'.github/workflows'/workflow).read_text())


if __name__ == '__main__': unittest.main()
