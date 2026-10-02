"""Official extract polygons select candidates; immutable markers select data."""
import hashlib
import json
import math
import re
from pathlib import Path


def in_ring(point, ring):
    x, y = point
    inside = False
    for i, (ax, ay) in enumerate(ring):
        bx, by = ring[i-1]
        # Include polygon boundaries. Neighboring extracts may both qualify.
        cross = (x-ax)*(by-ay) - (y-ay)*(bx-ax)
        if abs(cross) < 1e-10 and min(ax, bx) <= x <= max(ax, bx) and min(ay, by) <= y <= max(ay, by):
            return True
        if (ay > y) != (by > y) and x < (bx-ax)*(y-ay)/(by-ay)+ax:
            inside = not inside
    return inside


def contains(geometry, point):
    polygons = [geometry['coordinates']] if geometry['type'] == 'Polygon' else geometry['coordinates']
    return any(in_ring(point, poly[0]) and not any(in_ring(point, hole) for hole in poly[1:])
               for poly in polygons)


def locations(payload):
    points = payload.get('locations')
    if not isinstance(points, list) or len(points) != 2:
        raise ValueError('exactly two locations required')
    output = []
    for point in points:
        if not isinstance(point, dict):
            raise ValueError('invalid location')
        lat, lon = point.get('lat'), point.get('lon')
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in (lat, lon)):
            raise ValueError('invalid coordinates')
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError('coordinates outside earth')
        output.append((lon, lat))
    if payload.get('costing') != 'pedestrian':
        raise ValueError('only pedestrian routing supported')
    return output


class Catalog:
    def __init__(self, root, coverage):
        self.root = Path(root).resolve()
        self.features = {f['properties']['slug']: f for f in coverage['features']}

    def candidates(self, points):
        return sorted(slug for slug, f in self.features.items()
                      if all(contains(f['geometry'], p) for p in points))

    def available(self):
        regions = {}
        for active in (self.root / 'regions').glob('*/active.json'):
            pointer = json.loads(active.read_text())
            slug = active.parent.name
            regions[slug] = self.candidate(slug, pointer['fingerprint'])
        return regions

    def candidate(self, slug, fingerprint):
        if slug not in self.features or not re.fullmatch('[0-9a-f]{64}', fingerprint):
            raise ValueError('invalid installed graph identity')
        marker = self.root / 'regions' / slug / fingerprint / '.complete.json'
        for path in (marker, marker.parent, marker.parent.parent, marker.parent / 'tiles'):
            if path.is_symlink() or path.resolve() != path:
                raise ValueError('symlinked graph roots are forbidden')
        descriptor = json.loads(marker.read_text())
        tile_dir = Path(descriptor['tile_dir'])
        if not tile_dir.is_absolute():
            tile_dir = self.root / tile_dir
        expected = marker.parent / 'tiles'
        if tile_dir.resolve() != expected.resolve() or not expected.is_dir():
            raise ValueError('invalid installed graph path')
        if descriptor['fingerprint'] != fingerprint or descriptor['slug'] != slug:
            raise ValueError('graph marker identity mismatch')
        return dict(descriptor, tile_dir=str(tile_dir))

    @staticmethod
    def fingerprint(regions):
        return hashlib.sha256(json.dumps(sorted((s, r['fingerprint'])
                             for s, r in regions.items())).encode()).hexdigest()
