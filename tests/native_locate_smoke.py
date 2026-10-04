"""Actual pinned-engine locate protocol gate; invoked with existing Canada bytes."""
import math
from native_scope_geometry import locate_geometry, point


def locate_smoke(engine, region, location):
    reports = []
    for verbose in (False, True):
        response = engine.request(region, 'locate', dict(locations=[location],
                                  costing='pedestrian', verbose=verbose))
        assert isinstance(response, list) and len(response) == 1, 'LOCATE_RESPONSE_SHAPE'
        record = response[0]
        assert isinstance(record, dict), 'LOCATE_RECORD_SHAPE'
        assert record.get('input_lat') == round(location['lat'], 6), 'LOCATE_INPUT_LAT'
        assert record.get('input_lon') == round(location['lon'], 6), 'LOCATE_INPUT_LON'
        edges = record.get('edges')
        assert isinstance(edges, list) and edges, 'LOCATE_NONEMPTY_CANADA_EDGES'
        for edge in edges:
            assert isinstance(edge, dict), 'LOCATE_EDGE_SHAPE'
            assert point(dict(lat=edge.get('correlated_lat'), lon=edge.get('correlated_lon'))) is not None, 'LOCATE_CORRELATED_COORDINATES'
        facts = locate_geometry(response, location)
        assert facts['valid_projection_count'] == len(edges), 'LOCATE_SHARED_PARSER_PROTOCOL'
        minimum, maximum = facts['nearest_projection_m'], facts['farthest_projection_m']
        assert math.isfinite(minimum) and math.isfinite(maximum) and 0 <= minimum <= maximum, 'LOCATE_DISTANCE_FACTS'
        reports.append(dict(verbose=verbose, edge_count=len(edges), **facts))
    return reports
