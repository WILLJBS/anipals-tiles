"""Regression boundaries: diagnostic facts never become route acceptance."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import native_scope_smoke as smoke
from regional_engine import Engine, EngineError

GEOMETRY = dict(type='Polygon', coordinates=[[[-1,-1],[1,-1],[1,1],[-1,1],[-1,-1]]])
PROBE = dict(source_row=1, name='Public scope', lat=0, lng=0)


class ScopeFactsTests(unittest.TestCase):
    def run_case(self, responses, *, main=False, probes=None):
        owner = self
        probes = probes or [PROBE]
        class Fake:
            def __init__(self, *args): self.responses = iter(responses)
            def request(self, region, action, payload):
                if action == 'status': return dict(version='3.3.0', secret='never-export-this')
                value = next(self.responses)
                if isinstance(value, Exception): raise value
                return value
            def close(self): owner.closed = True
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name); self.root = root; self.closed = False
        (root/'tiles').mkdir()
        (root/'validation.json').write_text(json.dumps(dict(tile_hashes={'2/001.gph':'a'*64})))
        patches = [patch.object(smoke, 'Engine', Fake), patch.object(smoke.subprocess, 'check_output', return_value=b'{}')]
        for item in patches: item.start(); self.addCleanup(item.stop)
        if not main:
            return lambda: smoke.verify('test', root/'tiles', probes, GEOMETRY, root/'validation.json')
        (root/'spec.json').write_text(json.dumps(dict(builds=[dict(slug='test', probes=probes)])) )
        (root/'coverage.json').write_text(json.dumps(dict(features=[dict(properties=dict(slug='test'),geometry=GEOMETRY)])))
        args=['scope', '--slug','test','--tiles',str(root/'tiles'),'--spec',str(root/'spec.json'),
              '--coverage',str(root/'coverage.json'),'--validation',str(root/'validation.json'),
              '--output',str(root/'native-probes.json')]
        item=patch.object(sys,'argv',args);item.start();self.addCleanup(item.stop)
        return smoke.main

    def test_length_boundaries_unchanged_and_failures_retained(self):
        for length, passed in [(0,False),(-1,False),(5,False),(5.01,False),(0.001,True),(4.999,True)]:
            with self.subTest(length=length):
                call=self.run_case([dict(trip=dict(summary=dict(length=length)))]*4)
                if passed: self.assertEqual(call()[0]['distance_km'],length)
                else:
                    with self.assertRaisesRegex(ValueError,'no actual pedestrian'):call()
                record=json.loads((self.root/'native-scope-diagnostic.json').read_text())
                self.assertEqual(len(record['routes']),1 if passed else 4)
                self.assertEqual(record['routes'][0]['distance_km'],length)
                self.assertTrue(self.closed)

    def test_missing_length_is_not_zero_or_success(self):
        call=self.run_case([dict(trip=dict(summary={}))]*4)
        with self.assertRaises(ValueError):call()
        record=json.loads((self.root/'native-scope-diagnostic.json').read_text())
        self.assertEqual([x['classification'] for x in record['routes']],['missing_length']*4)

    def test_no_success_artifact_on_failed_main_but_diagnostics_survive(self):
        call=self.run_case([dict(trip=dict(summary=dict(length=0)))]*4,main=True)
        with self.assertRaises(ValueError):call()
        self.assertFalse((self.root/'native-probes.json').exists())
        self.assertTrue((self.root/'native-scope-diagnostic.json').exists())

    def test_partial_scope_success_never_creates_acceptance_proof(self):
        good=dict(trip=dict(summary=dict(length=.2)))
        bad=dict(trip=dict(summary=dict(length=0)))
        call=self.run_case([good]+[bad]*4, main=True, probes=[PROBE,dict(PROBE,source_row=2)])
        with self.assertRaises(ValueError):call()
        self.assertFalse((self.root/'native-probes.json').exists())
        record=json.loads((self.root/'native-scope-diagnostic.json').read_text())
        self.assertEqual([r['source_row'] for r in record['routes']],[1,2,2,2,2])

    def test_main_success_proof_shape_unchanged(self):
        call=self.run_case([dict(trip=dict(summary=dict(length=.2)))],main=True)
        with contextlib.redirect_stdout(io.StringIO()):call()
        value=json.loads((self.root/'native-probes.json').read_text())
        self.assertEqual(set(value),{'slug','native_version','scope_sha256','probes'})
        self.assertEqual(value['probes'],[dict(source_row=1,name='Public scope',distance_km=.2,verified=True)])

    def test_404_continues_503_aborts_and_no_raw_error_or_payload_leaks(self):
        for status in (404,503):
            error=EngineError('never-export-this',status,native_code=171,native_exit_code=1)
            call=self.run_case([error]*4)
            with self.assertRaises(ValueError if status==404 else EngineError):call()
            text=(self.root/'native-scope-diagnostic.json').read_text();record=json.loads(text)
            self.assertNotIn('never-export-this',text)
            self.assertNotIn('"lat"',text);self.assertNotIn('"lon"',text)
            self.assertEqual(len(record['routes']),4 if status==404 else 1)
            self.assertEqual(record['routes'][0]['native_code'],171)
            self.assertEqual(record['routes'][0]['error_status'],status)

    def test_nonfinite_and_untrusted_values_never_enter_diagnostic_json(self):
        from native_scope_records import response_record
        for value,category in [(float('nan'),'nonfinite_length'),(float('inf'),'nonfinite_length'),
                               ('never-export-this','invalid_length')]:
            record=response_record('route',dict(trip=dict(summary=dict(length=value))))
            self.assertEqual(record['classification'],category)
            self.assertNotIn('never-export-this',json.dumps(record,allow_nan=False))
        self.assertEqual(response_record('status',dict(version='never-export-this'))['native_version'],None)

    def test_outside_offsets_are_logged_without_calls_or_success(self):
        call=self.run_case([])
        with patch.object(smoke,'contains',return_value=False):
            with self.assertRaises(ValueError):call()
        record=json.loads((self.root/'native-scope-diagnostic.json').read_text())
        self.assertEqual([r['classification'] for r in record['routes']],['outside_coverage']*4)


class EngineMetadataTests(unittest.TestCase):
    def test_metadata_only_accepts_integers_and_preserves_legacy_exception(self):
        error=EngineError('same public message',404,native_code='never-export-this',native_exit_code={'raw':'secret'})
        self.assertEqual(str(error),'same public message');self.assertEqual(error.status,404)
        self.assertIsNone(error.native_code);self.assertIsNone(error.native_exit_code)

    def test_real_child_error_code_retained_without_changing_mapping_or_message(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve();tiles=root/'tiles';tiles.mkdir()
            region=dict(slug='test',fingerprint='a'*64,tile_dir=str(tiles))
            (root/'.complete.json').write_text(json.dumps(region))
            child=root/'fake-native.py'
            child.write_text('import sys,json\nprint(json.dumps({"error_code":int(sys.argv[1]),"error":"never-export-this"}))\nsys.exit(1)\n')
            launch=subprocess.Popen
            for code,status,message in [(171,404,'no suitable route in regional graph'),(999,503,'native graph request failed')]:
                engine=Engine(dict(mjolnir={},loki={},thor={}),root/'config')
                try:
                    with patch('regional_engine.subprocess.Popen',side_effect=lambda command,**kw:launch([sys.executable,str(child),str(code)],**kw)):
                        with self.assertRaises(EngineError) as caught:engine.request(region,'route',{})
                    self.assertEqual(caught.exception.status,status)
                    self.assertEqual(str(caught.exception),message)
                    self.assertEqual(caught.exception.native_code,code)
                    self.assertEqual(caught.exception.native_exit_code,1)
                finally:engine.close()


if __name__=='__main__':unittest.main()
