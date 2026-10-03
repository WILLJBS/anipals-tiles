import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('global_plan', Path(__file__).resolve().parents[1] / 'tools/global_coverage_plan.py')
planner = importlib.util.module_from_spec(spec); spec.loader.exec_module(planner)


def feature(identifier, country=None):
    properties = dict(id=identifier, urls=dict(pbf='https://download.geofabrik.de/europe/'+identifier+'-latest.osm.pbf'))
    if country:
        properties['iso3166-1:alpha2'] = [country]
    return dict(type='Feature', properties=properties, geometry=dict(type='Polygon',
        coordinates=[[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]))


class GlobalPlanTests(unittest.TestCase):
    def test_granular_extract_wins_over_aggregate_and_country_overlap(self):
        city = dict(name='city', country='LI', lat=.5, lng=.5)
        index = dict(features=[feature('alps'), feature('neighbor', 'CH'), feature('liechtenstein', 'LI')])
        result = planner.plan([city], dict(features=[]), index)
        self.assertEqual(result['new_extracts'][0]['source_id'], 'liechtenstein')
        self.assertEqual(result['custom_extract_required'], [])

    def test_aggregate_only_is_never_claimed_as_buildable_city(self):
        city = dict(name='island', country='XX', lat=.5, lng=.5)
        result = planner.plan([city], dict(features=[]), dict(features=[feature('south-america')]))
        self.assertEqual(result['new_extracts'], [])
        self.assertEqual(len(result['custom_extract_required']), 1)
        self.assertEqual(result['existing_polygon_rows'], 0)
        self.assertEqual(result['acceptance'], 'polygon-planning-only')

    def test_existing_graph_reuse_and_no_official_coverage_are_separate(self):
        cities = [dict(name='existing', lat=.5, lng=.5), dict(name='outside', lat=3, lng=3)]
        result = planner.plan(cities, dict(features=[feature('existing')]), dict(features=[]))
        self.assertEqual(result['existing_polygon_rows'], 1)
        self.assertEqual(result['outside_all_official_polygons'][0]['name'], 'outside')


if __name__ == '__main__':
    unittest.main()
