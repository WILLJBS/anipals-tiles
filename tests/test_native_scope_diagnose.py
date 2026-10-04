import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from native_scope_diagnose import diagnose, EngineError


class ScopeDiagnosticTests(unittest.TestCase):
    def fixture(self):
        probe=dict(source_row=6087,name='Bakwa',lat=4.1263,lng=27.3953)
        feature=dict(properties=dict(slug='congo'),geometry=dict(type='Polygon',coordinates=[[[27,4],[28,4],[28,5],[27,5],[27,4]]]))
        return probe,feature,dict(tile_hashes={'2/001.gph':'a'*64})

    def test_preserves_zero_route_and_correlation_evidence_without_acceptance(self):
        calls=[]
        class Fake:
            def __init__(self,*args):pass
            def request(self,region,action,payload):
                calls.append((action,copy.deepcopy(payload)))
                if action=='status':return dict(version='3.3.0')
                if action=='locate':return [dict(edges=[dict(way_id=7,correlated_lat=4.2,correlated_lon=27.2)])]
                return dict(trip=dict(summary=dict(length=0),locations=payload['locations']))
            def close(self):calls.append(('close',{}))
        with tempfile.TemporaryDirectory() as folder:
            probe,feature,validation=self.fixture();original=copy.deepcopy(probe)
            result=diagnose('congo',Path(folder)/'tiles',probe,feature,validation,{},Fake)
            self.assertEqual([r['classification'] for r in result['routes']],['zero_length']*4)
            self.assertEqual(len(result['points']),5);self.assertEqual(probe,original)
            self.assertFalse(result['changes_to_source_or_acceptance'])
            self.assertFalse((Path(folder)/'.complete.json').exists())
            self.assertEqual(calls[-1][0],'close')
            serialized=json.dumps(result)
            for forbidden in ('projected', 'requested', '"lat"', '"lon"', 'way_id'):
                self.assertNotIn(forbidden,serialized)
            self.assertEqual(result['points'][0]['correlation']['edge_count'],1)
            self.assertEqual(result['points'][0]['correlation']['valid_projection_count'],1)

    def test_distinguishes_no_edge_errors_and_refuses_existing_marker(self):
        class Fake:
            def __init__(self,*args):pass
            def request(self,region,action,payload):
                if action=='status':return dict(version='3.3.0')
                raise EngineError('private raw payload must not escape',404,native_code=171,native_exit_code=1)
            def close(self):pass
        with tempfile.TemporaryDirectory() as folder:
            probe,feature,validation=self.fixture();root=Path(folder)
            result=diagnose('congo',root/'tiles',probe,feature,validation,{},Fake)
            self.assertTrue(all(r['classification']=='native_error' and r['error_status']==404 for r in result['routes']))
            self.assertTrue(all(r['native_code']==171 and r['native_exit_code']==1 for r in result['routes']))
            self.assertNotIn('private raw payload',json.dumps(result))
            (root/'.complete.json').write_text('existing')
            with self.assertRaises(ValueError):diagnose('congo',root/'tiles',probe,feature,validation,{},Fake)
            self.assertEqual((root/'.complete.json').read_text(),'existing')

    def test_constructor_and_close_failures_remove_owned_marker(self):
        for where in ('constructor', 'close'):
            class Failing:
                def __init__(self, *args):
                    if where == 'constructor': raise RuntimeError('constructor')
                def request(self, region, action, payload):
                    if action == 'status': return dict(version='3.3.0')
                    return {}
                def close(self): raise RuntimeError('close')
            with tempfile.TemporaryDirectory() as folder:
                probe, feature, validation = self.fixture()
                with self.assertRaisesRegex(RuntimeError, where):
                    diagnose('congo', Path(folder)/'tiles', probe, feature, validation, {}, Failing)
                self.assertFalse((Path(folder)/'.complete.json').exists())
                record=json.loads((Path(folder)/'native-scope-diagnostic.json').read_text())
                self.assertEqual(record['kind'],'native-scope-diagnostic-not-acceptance')
                self.assertNotIn('constructor',json.dumps(record))


if __name__ == '__main__':unittest.main()
