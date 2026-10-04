"""Allowlisted native diagnostics, never an acceptance decision or raw output."""
import math

OFFSETS = ((.002, .002), (.002, -.002), (-.002, .002), (-.002, -.002))


def error_record(error):
    return dict(classification='native_error', error_status=error.status,
                native_code=error.native_code, native_exit_code=error.native_exit_code)


def response_record(action, response):
    if action == 'status':
        matched = isinstance(response, dict) and response.get('version') == '3.3.0'
        return dict(classification='expected_abi' if matched else 'unexpected_abi',
                    native_version='3.3.0' if matched else None)
    if action == 'locate':
        if not isinstance(response, list) or not response or not isinstance(response[0], dict):
            return dict(classification='invalid_locate')
        edges = response[0].get('edges')
        return dict(classification='edges_found' if isinstance(edges, list) and edges else 'no_edges',
                    edge_count=len(edges) if isinstance(edges, list) else 0)
    trip = response.get('trip') if isinstance(response, dict) else None
    summary = trip.get('summary') if isinstance(trip, dict) else None
    length = summary.get('length') if isinstance(summary, dict) else None
    if length is None:
        return dict(classification='missing_length')
    if type(length) not in (int, float):
        return dict(classification='invalid_length')
    if not math.isfinite(length):
        return dict(classification='nonfinite_length')
    category = ('zero_length' if length == 0 else 'negative_length' if length < 0
                else 'length_at_or_above_limit' if length >= 5 else 'nonzero_route')
    return dict(classification=category, distance_km=length)
