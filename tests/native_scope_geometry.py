"""Numeric-only correlation evidence. Never exports coordinates or route shapes."""
import math


def point(value):
    if not isinstance(value, dict): return None
    lat, lon = value.get("lat"), value.get("lon")
    if (type(lat) not in (int, float) or type(lon) not in (int, float)
            or not math.isfinite(lat) or not math.isfinite(lon)
            or not -90 <= lat <= 90 or not -180 <= lon <= 180): return None
    return lat, lon


def distance(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2-lat1)/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2
    return round(6371000*2*math.asin(math.sqrt(min(1, max(0, h)))), 3)


def locate_geometry(response, requested):
    edges = response[0].get("edges") if isinstance(response, list) and response and isinstance(response[0], dict) else None
    origin = point(requested)
    projections = [point(edge.get("projected")) for edge in edges if isinstance(edge, dict)] if isinstance(edges, list) else []
    distances = [distance(origin, p) for p in projections if origin and p]
    return dict(valid_projection_count=len(distances), nearest_projection_m=min(distances) if distances else None,
                farthest_projection_m=max(distances) if distances else None)


def endpoints(shape):
    # Valhalla's default is polyline6; coordinates remain process-local.
    if not isinstance(shape, str) or not 0 < len(shape) <= 2000000: return None
    cursor, lat, lon, count = 0, 0, 0, 0
    first = last = None
    while cursor < len(shape):
        deltas = []
        for _ in range(2):
            value, shift = 0, 0
            while True:
                if cursor >= len(shape) or shift > 30: return None
                byte = ord(shape[cursor])-63; cursor += 1
                if not 0 <= byte <= 63: return None
                value |= (byte & 31) << shift; shift += 5
                if byte < 32: break
            deltas.append(~(value >> 1) if value & 1 else value >> 1)
        lat += deltas[0]; lon += deltas[1]
        current = point(dict(lat=lat/1e6, lon=lon/1e6))
        if current is None: return None
        if first is None: first = current
        last = current; count += 1
    return first, last, count


def route_geometry(response, requested):
    trip = response.get("trip") if isinstance(response, dict) else None
    legs = trip.get("legs") if isinstance(trip, dict) else None
    if not isinstance(legs, list) or not legs: return dict(geometry_status="missing")
    decoded = [endpoints(leg.get("shape")) if isinstance(leg, dict) else None for leg in legs]
    if any(p is None for p in decoded): return dict(geometry_status="invalid")
    start, end = point(requested[0]), point(requested[-1])
    if start is None or end is None: return dict(geometry_status="invalid_request")
    first, last = decoded[0][0], decoded[-1][1]
    return dict(geometry_status="decoded_polyline6", shape_point_count=sum(p[2] for p in decoded),
                start_offset_m=distance(start, first), end_offset_m=distance(end, last),
                shape_endpoint_separation_m=distance(first, last), same_shape_endpoint=first == last)
