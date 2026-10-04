"""Numeric native evidence and unchanged gate orchestration boundaries."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from native_scope_geometry import endpoints, locate_geometry, route_geometry
import native_scope_diagnose as diagnostic


def encode(points):
    result = ""; previous = [0, 0]
    for pair in points:
        for axis, val in enumerate(pair):
            value = round(val*1e6); diff = value-previous[axis]; previous[axis] = value
            diff = ~(diff << 1) if diff < 0 else diff << 1
            while diff >= 32:
                result += chr((32 | (diff & 31))+63); diff >>= 5
            result += chr(diff+63)
    return result


class NumericFactsTests(unittest.TestCase):
    def test_projection_distance_without_coordinate_or_edge_leak(self):
        value = locate_geometry([dict(edges=[
            dict(projected=dict(lat=0,lon=.2), way_id="private-edge"),
            dict(projected=dict(lat=0,lon=.1)),
            dict(projected=dict(lat=float("nan"),lon=0)),
            dict(projected=dict(lat=0,lon=999)),
        ])], dict(lat=0,lon=0))
        self.assertEqual(value["valid_projection_count"], 2)
        self.assertAlmostEqual(value["nearest_projection_m"], 11119.493, places=3)
        self.assertAlmostEqual(value["farthest_projection_m"], 22238.985, places=3)
        for forbidden in ('"lat"', '"lon"', "private-edge", "projected"):
            self.assertNotIn(forbidden, json.dumps(value, allow_nan=False))

    def test_absence_and_bad_shapes_are_explicit_not_zero_distance(self):
        self.assertIsNone(locate_geometry([], dict(lat=0,lon=0))["nearest_projection_m"])
        for shape in (None, "", "_", "~~~~~~~?", "\x00", encode([(91,0)])):
            self.assertIsNone(endpoints(shape))
            value=route_geometry(dict(trip=dict(legs=[dict(shape=shape)])), [dict(lat=0,lon=0)]*2)
            self.assertEqual(value, dict(geometry_status="invalid"))
        self.assertEqual(route_geometry({}, []), dict(geometry_status="missing"))

    def test_polyline6_precision_multileg_and_duplicate_endpoint(self):
        a, b, c = (4.123456, -73.654321), (4.124456, -73.654321), (4.125456, -73.654321)
        req=[dict(lat=a[0],lon=a[1]),dict(lat=c[0],lon=c[1])]
        shape=encode([a,b])
        self.assertEqual(endpoints(shape), (a,b,2))
        value=route_geometry(dict(trip=dict(legs=[dict(shape=shape),dict(shape=encode([b,c]))])),req)
        self.assertEqual(value["start_offset_m"],0); self.assertEqual(value["end_offset_m"],0)
        self.assertEqual(value["shape_point_count"],4); self.assertFalse(value["same_shape_endpoint"])
        zero=route_geometry(dict(trip=dict(legs=[dict(shape=encode([b,b]))])),req)
        self.assertTrue(zero["same_shape_endpoint"])
        self.assertGreater(zero["start_offset_m"],100); self.assertGreater(zero["end_offset_m"],100)
        self.assertEqual(zero["shape_endpoint_separation_m"],0)
        for forbidden in (shape, '"lat"', '"lon"', '"shape"'):
            self.assertNotIn(forbidden,json.dumps(value))

    def test_request_wrapper_records_geometry_and_native_error_metadata(self):
        class Fake:
            def __init__(self,*args): pass
            def close(self): pass
            def request(self, region, action, payload):
                if action=="status": return dict(version="3.3.0")
                if action=="locate": return [dict(edges=[dict(projected=dict(lat=0,lon=.1))])]
                if payload["locations"][1]["lon"] < 0:
                    raise diagnostic.EngineError("raw secret",404,native_code=171,native_exit_code=1)
                return dict(trip=dict(summary=dict(length=0),legs=[dict(shape=encode([(0,.1),(0,.1)]))]))
        with tempfile.TemporaryDirectory() as folder:
            value=diagnostic.diagnose("test",Path(folder)/"tiles",dict(source_row=7,lat=0,lng=0),
                dict(geometry=dict(type="Polygon",coordinates=[[[-1,-1],[1,-1],[1,1],[-1,1],[-1,-1]]])),
                dict(tile_hashes={"2/001.gph":"a"*64}),{},Fake)
        self.assertEqual(value["routes"][0]["classification"],"zero_length")
        self.assertTrue(value["routes"][0]["same_shape_endpoint"])
        self.assertGreater(value["routes"][0]["start_offset_m"],11000)
        self.assertEqual(value["routes"][1]["error_status"],404)
        self.assertEqual(value["routes"][1]["native_code"],171)
        self.assertNotIn("raw secret",json.dumps(value))


class GateWiringTests(unittest.TestCase):
    def test_gate_selection_keeps_failed_attempt_even_when_later_offset_passed(self):
        scope=dict(probes=[dict(source_row=1),dict(source_row=2)])
        validation=dict(tile_hashes={"2/001.gph":"a"*64})
        gate=dict(slug="test",scope_sha256=diagnostic.canonical_hash(scope["probes"]),
                  graph_fingerprint=diagnostic.canonical_hash(validation["tile_hashes"]),
                  routes=[dict(source_row=1,classification="zero_length"),
                          dict(source_row=1,classification="nonzero_route"),
                          dict(source_row=2,classification="nonzero_route")])
        self.assertEqual(diagnostic.select_probes(scope,gate,validation,"test"),[scope["probes"][0]])
        for field in ("slug","scope_sha256","graph_fingerprint"):
            with self.assertRaisesRegex(ValueError,"identity"):
                diagnostic.select_probes(scope,dict(gate,**{field:"wrong"}),validation,"test")
        gate["routes"].append(dict(source_row=99,classification="zero_length"))
        with self.assertRaisesRegex(ValueError,"source rows"):
            diagnostic.select_probes(scope,gate,validation,"test")

    def test_actual_shell_preserves_failure_even_when_diagnosis_succeeds(self):
        workflow=(Path(__file__).resolve().parents[1]/".github/workflows/build.yml").read_text()
        script=workflow.split("              gate_status=0\n",1)[1].split("            '",1)[0]
        script="set -e\ngate_status=0\n"+script
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); (root/"native-scope-diagnostic.json").write_text("{}")
            for status in (0,22):
                prefix='python3() { if [[ "$1" == *native_scope_smoke.py ]]; then return '+str(status)+'; fi; return 0; }\n'
                result=subprocess.run(["bash","-c",prefix+script],cwd=root,capture_output=True,text=True)
                self.assertEqual(result.returncode,status)
        self.assertIn("native-correlation-*.json",workflow)


if __name__ == "__main__": unittest.main()
