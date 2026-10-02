"""Read actual local-road node coordinates for extracts without audited cities.
Uses the same strict 3.3.0 ABI as validate_tiles; never invent a bbox midpoint.
"""
from pathlib import Path
import struct

from validate_tiles import header


def graph_points(root, limit=3):
    count = 0
    for path in sorted((Path(root) / '2').rglob('*.gph')):
        with path.open('rb') as source:
            h = header(source.read(272), path.stat().st_size)
            tile_id = h['graph'] >> 3
            lon, lat = (tile_id % 1440) * .25 - 180, (tile_id // 1440) * .25 - 90
            for _ in range(h['nodes']):
                data = source.read(32)
                position, edges = struct.unpack_from('<QQ', data)
                if not (position >> 52 & 2) or not (edges >> 21 & 127):
                    continue
                yield {'lat': lat + (position & ((1 << 22)-1)) * 1e-6 + (position >> 22 & 15) * 1e-7,
                       'lng': lon + (position >> 26 & ((1 << 22)-1)) * 1e-6 + (position >> 48 & 15) * 1e-7}
                count += 1
                if count >= limit:
                    return
                break
