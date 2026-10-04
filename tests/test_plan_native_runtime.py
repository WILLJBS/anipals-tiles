"""Offline planner shares the catalog verifier; this is not real source evidence."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from native_runtime_fixture import request,blob
from plan_native_runtime import plan,read_ref
from runtime_index_assembler import canonical_bytes


class PlannerTests(unittest.TestCase):
    def fixture(self):
        source=request();feature=dict(type='Feature',properties=dict(slug=source['slug']),
            geometry=dict(type='Polygon',coordinates=[[[0,0],[2,0],[2,2],[0,2],[0,0]]]))
        catalog=dict(regions=[dict(slug=source['slug'],feature=feature)])
        probe=dict(schema=1,slug=source['slug'],provenance='Synthetic fixture only',
                   payload=dict(costing='pedestrian',locations=[dict(lat=1,lon=1),dict(lat=1.001,lon=1.001)]))
        data=[canonical_bytes(catalog),b'{}',canonical_bytes(probe),b'child']
        refs=[blob(raw) for raw in data];bodies={ref['sha256']:raw for ref,raw in zip(refs,data)}
        class Mirror:
            def read(self,ref,*_):return bodies[ref['sha256']]
        def shared(raw,sha,objects,root):
            self.assertEqual((raw,sha),(b'{}',refs[1]['sha256']))
            objects.read(refs[3],100);objects.read(refs[3],100)
            return catalog
        return source,refs,Mirror(),shared
    def test_exact_tree_collected_from_shared_verifier_and_deduplicated(self):
        source,refs,objects,shared=self.fixture()
        with patch('plan_native_runtime.catalog_plan',side_effect=shared) as verify:
            result=plan(source['source_sha'],*refs[:3],objects,source['slug'],source['budgets'])
        verify.assert_called_once();self.assertEqual(result['objects'],[refs[3]])
        self.assertEqual(result['catalog'],refs[0]);self.assertEqual(result['probe'],refs[2])
    def test_shared_incomplete_group_rejection_is_not_bypassed_by_single_slug(self):
        source,refs,objects,_=self.fixture()
        with patch('plan_native_runtime.catalog_plan',side_effect=ValueError('COMPLETE_SELECTED_RECEIPT_ROSTER_REQUIRED')):
            with self.assertRaisesRegex(ValueError,'COMPLETE_SELECTED'):
                plan(source['source_sha'],*refs[:3],objects,source['slug'],source['budgets'])
    def test_descriptor_files_reject_duplicate_json_and_oversize(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'ref'
            for raw in (b'{"bytes":1,"bytes":2}',b'x'*8193):
                path.write_bytes(raw)
                with self.assertRaises(ValueError):read_ref(path)


if __name__=='__main__':unittest.main()
