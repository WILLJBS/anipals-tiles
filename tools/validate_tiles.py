#!/usr/bin/env python3
"""Fail-closed V3 road-tile index/offset gate (little-endian, levels 0..2).
ABI source: valhalla/valhalla tag 3.3.0 baldr/{graphtileheader,nodeinfo,
directededge}.h and src/baldr/graphtile.cc. This is NOT a route/export smoke:
it checks every node edge span, transition span, edge-info offset, end-node
and spatial-bin edge reference. Missing cross-region tiles are counted, not
invented: a Geofabrik partition legitimately ends at its boundary.
Does not validate routing topology, variable restriction bodies or text grammar.
The pinned image and these ABI assumptions must be reviewed together on upgrade.
"""
import argparse
import hashlib
import json
import mmap
import struct
from pathlib import Path

MASK21 = (1 << 21) - 1
MASK46 = (1 << 46) - 1
TILE_MASK = (1 << 25) - 1


def u64(data, offset):
    return struct.unpack_from('<Q', data, offset)[0]


def header(data, size):
    if size < 272:
        raise ValueError('truncated header')
    version = bytes(data[16:32]).split(b'\0')[0].decode('ascii')
    if version != '3.3.0':
        raise ValueError('unsupported tile ABI: ' + version)
    if struct.unpack_from('<I', data, 224)[0] != size:
        raise ValueError('header end_offset != file size')
    graph = u64(data, 0) & MASK46
    if graph & 7 > 2 or graph >> 25:
        raise ValueError('unsupported road tile graphid')
    counts = u64(data, 40)
    nodes, edges = counts & MASK21, (counts >> 21) & MASK21
    transitions, turns = struct.unpack_from('<II', data, 48)
    transitions &= (1 << 22) - 1
    turns &= MASK21
    w56, w64, w72 = u64(data, 56), u64(data, 64), u64(data, 72)
    # Transit records have separate ABIs: refuse them rather than silently skip.
    if (w56 & 65535) or ((w56 >> 23) & ((1 << 24) - 1)) or (w56 >> 47) or (w64 & ((1 << 24) - 1)):
        raise ValueError('transit records unsupported by road-tile gate')
    signs, access, admins = (w64 >> 24) & ((1 << 24) - 1), w72 & ((1 << 24) - 1), (w72 >> 24) & 65535
    edge_start = 272 + nodes * 32 + transitions * 8
    bin_start = edge_start + edges * (48 + 8 * (u64(data, 0) >> 63)) + access * 16 + signs * 8 + turns * 8 + admins * 16
    offsets = struct.unpack_from('<4I', data, 96)
    bins = struct.unpack_from('<25I', data, 116)
    lane, predicted = struct.unpack_from('<II', data, 216)
    if list(offsets) != sorted(offsets) or not (bin_start <= offsets[0] <= offsets[-1] <= lane <= size):
        raise ValueError('invalid section offsets')
    if predicted and not lane <= predicted <= size:
        raise ValueError('invalid predicted-speed offset')
    if list(bins) != sorted(bins) or bin_start + bins[-1] * 8 != offsets[0]:
        raise ValueError('invalid spatial-bin offsets')
    return dict(graph=graph, nodes=nodes, edges=edges, transitions=transitions,
                edge_start=edge_start, bin_start=bin_start, bins=bins[-1],
                edgeinfo_size=offsets[3] - offsets[2])


def validate(root):
    paths = sorted(Path(root).rglob('*.gph'))
    if not paths:
        raise ValueError('empty tileset')
    tiles = {}
    for path in paths:
        with path.open('rb') as f:
            h = header(f.read(272), path.stat().st_size)
        parts = path.relative_to(root).with_suffix('').parts
        expected = int(''.join(parts[1:]))
        if h['graph'] >> 3 != expected or h['graph'] & 7 != int(parts[0]):
            raise ValueError(str(path) + ': path/header graphid mismatch')
        if h['graph'] in tiles:
            raise ValueError('duplicate graphid')
        tiles[h['graph']] = (path, h)
    missing = 0

    def ref(value, kind):
        nonlocal missing
        graph = value & MASK46
        tile = tiles.get(graph & TILE_MASK)
        if tile is None:
            missing += 1
        elif graph >> 25 >= tile[1][kind]:
            raise ValueError('%s index %d out of bounds %d in tile %d' %
                             (kind, graph >> 25, tile[1][kind], graph & TILE_MASK))

    for path, h in tiles.values():
        try:
            with path.open('rb') as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as data:
                for i in range(h['nodes']):
                    w = u64(data, 272 + i * 32 + 8)
                    start, count = w & MASK21, (w >> 21) & 127
                    if start + count > h['edges']:
                        raise ValueError('node %d edge span %d+%d exceeds %d' % (i, start, count, h['edges']))
                    tr = u64(data, 272 + i * 32 + 16)
                    if (tr & MASK21) + ((tr >> 21) & 7) > h['transitions']:
                        raise ValueError('node transition span out of bounds')
                    if ((w >> 28) & 4095) >= h_admin_count(data) and h_admin_count(data):
                        raise ValueError('node admin index out of bounds')
                for i in range(h['transitions']):
                    ref(u64(data, 272 + h['nodes'] * 32 + i * 8), 'nodes')
                for i in range(h['edges']):
                    pos = h['edge_start'] + i * 48
                    ref(u64(data, pos), 'nodes')
                    if u64(data, pos + 8) & TILE_MASK >= h['edgeinfo_size']:
                        raise ValueError('edge-info offset out of bounds')
                for i in range(h['bins']):
                    ref(u64(data, h['bin_start'] + i * 8), 'edges')
        except (ValueError, struct.error) as error:
            raise ValueError(str(path) + ': ' + str(error)) from error
    return dict(validator='gph-v3-index-v1', tiles=len(tiles), external_references=missing,
                tile_hashes={str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths})


def h_admin_count(data):
    return (u64(data, 72) >> 24) & 65535


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('tiles')
    args = parser.parse_args()
    try:
        print(json.dumps(validate(args.tiles)))
    except (ValueError, OSError) as error:
        parser.exit(1, str(error) + '\n')
