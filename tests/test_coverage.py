import copy
import json
import unittest
import test_safety as safety
from test_safety import MANIFEST, ROOT

COVERAGE = MANIFEST.COVERAGE


class CoverageTests(unittest.TestCase):
    def test_committed_exact_roster_and_us_composites(self):
        roster = json.loads((ROOT/'deploy/regions.json').read_text())
        polygons = COVERAGE.validate(json.loads((ROOT/'deploy/coverage.json').read_text()), roster)
        self.assertEqual(len(polygons), 61)
        for area in ('west','south','midwest','northeast','pacific'):
            feature = polygons['north-america-us-'+area]
            self.assertEqual(feature['properties']['source_id'], 'us-'+area)
            self.assertEqual(feature['properties']['pbf_url'],
                             'https://download.geofabrik.de/north-america/us-'+area+'-latest.osm.pbf')
        # Pacific official geometry includes Alaska/Aleutian longitude seam pieces.
        points = [point for polygon in polygons['north-america-us-pacific']['geometry']['coordinates']
                  for ring in polygon for point in ring]
        self.assertTrue(any(p[0] == -180 for p in points))
        self.assertTrue(any(p[0] == 180 for p in points))

    def test_exact_url_not_ambiguous_id_and_geometry_unchanged(self):
        roster = {'region':[{'slug':'one','region':'north-america/us-west'}]}
        geometry = {'type':'MultiPolygon','coordinates':[[[[0,0],[1,0],[1,1],[0,0]]]]}
        feature = dict(type='Feature', properties=dict(id='us-west',urls=dict(
            pbf='https://download.geofabrik.de/north-america/us-west-latest.osm.pbf')),geometry=geometry)
        index = dict(type='FeatureCollection', features=[feature])
        output = COVERAGE.build(index,roster,'a'*64)
        self.assertEqual(output['features'][0]['geometry'],geometry)
        feature['properties']['urls']['pbf'] = 'https://download.geofabrik.de/north-america/us-latest.osm.pbf'
        with self.assertRaisesRegex(ValueError,'exact official'): COVERAGE.build(index,roster,'a'*64)

    def test_malformed_or_mismatched_coverage_fails_closed(self):
        fixture = safety.ReleaseTests(); _,roster,_ = fixture.fixture()
        original = fixture.coverage(roster)
        for change in ('duplicate','pbf','digest','open-ring','coordinate'):
            coverage = copy.deepcopy(original); feature=coverage['features'][0]
            if change=='duplicate': coverage['features'].append(feature)
            if change=='pbf': feature['properties']['pbf_url']='https://example.com/a'
            if change=='digest': feature['properties']['source_index_sha256']='b'*64
            if change=='open-ring': feature['geometry']['coordinates'][0][-1]=[0,1]
            if change=='coordinate': feature['geometry']['coordinates'][0][1]=[181,0]
            with self.assertRaises(ValueError): COVERAGE.validate(coverage,roster)

    def test_regional_ready_still_rejects_invalid_inventory_and_source(self):
        fixture=safety.ReleaseTests(); release,roster,manifest=fixture.fixture(); coverage=fixture.coverage(roster)
        for change in ('path','hash','count','source','coverage','validator'):
            modified=copy.deepcopy(manifest)
            if change=='path': modified['validation']['tile_hashes']={'../evil.gph':'a'*64}
            if change=='hash': modified['validation']['tile_hashes']={'2/000/000/001.gph':'bad'}
            if change=='count': modified['validation']['tiles']=2
            if change=='source': modified['pbf_url']='wrong'
            if change=='coverage': modified['coverage_sha256']='0'*64
            if change=='validator': modified['validation']['validator']='unchecked'
            with self.assertRaises(ValueError): MANIFEST.ready(release,roster,[modified],'pinned',coverage)


if __name__ == '__main__': unittest.main()
