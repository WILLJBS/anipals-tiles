"""Transport success must also describe the requested endpoints, not a distant route."""
from types import SimpleNamespace
import unittest
from native_r2_acceptance import Counter, route_pair
from test_native_scope_geometry import encode


class NativeEndpointGate(unittest.TestCase):
    def pair(self, points, target=.002):
        counter = Counter()
        class Engine:
            calls = 0
            def request(self, *_):
                self.calls += 1
                if self.calls == 1:
                    counter.before_send()
                    list(counter.tile_reader(lambda _: [b'fixture'], 'test/', {'tile': {}})('test/tile'))
                return dict(trip=dict(units='kilometers', summary=dict(length=.25),
                                      legs=[dict(shape=encode(points))]))
        payload = dict(locations=[dict(lat=0, lon=0), dict(lat=0, lon=target)])
        return route_pair(Engine(), {}, payload, counter, SimpleNamespace(failures=0))

    def test_distant_nonzero_geometry_is_rejected_despite_valid_cold_hot_io(self):
        with self.assertRaisesRegex(ValueError, 'ENDPOINT'):
            self.pair([(50, 10), (50, 10.002)])

    def test_short_probe_quarter_distance_is_not_replaced_with_absolute_500m(self):
        with self.assertRaisesRegex(ValueError, 'ENDPOINT'):
            self.pair([(0, .001), (0, .003)])

    def test_collapsed_shape_is_not_saved_by_positive_summary(self):
        with self.assertRaisesRegex(ValueError, 'ENDPOINT'):
            self.pair([(0, .001), (0, .001)])

    def test_matching_geometry_retains_private_numeric_evidence(self):
        records = self.pair([(0, 0), (0, .002)])
        self.assertEqual(records[0]['start_offset_m'], 0)
        self.assertEqual(records[0]['end_offset_m'], 0)
        self.assertLess(records[0]['endpoint_limit_m'], 500)


if __name__ == '__main__':
    unittest.main()
