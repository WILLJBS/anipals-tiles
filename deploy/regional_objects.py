"""Immutable object inventories; GraphIds remain scoped to one regional graph."""
import hashlib
import json
import re

from regional_release import canonical_hash, sha

TILE = re.compile(r'[012]/(?:[0-9]{3}/)*[0-9]{3}\.gph')
SLUG = re.compile(r'[a-z0-9]+(?:-[a-z0-9]+)*')


class ObjectCatalog:
    def __init__(self, manifests, image):
        """manifests: (raw bytes, trusted SHA256) from an authenticated release."""
        if not re.fullmatch(r'valhalla/valhalla@sha256:[0-9a-f]{64}', image):
            raise ValueError('pinned image required')
        self.graphs = {}
        for raw, expected in manifests:
            if not sha(expected) or hashlib.sha256(raw).hexdigest() != expected:
                raise ValueError('object inventory digest mismatch')
            value = json.loads(raw)
            slug, fingerprint = value.get('slug'), value.get('graph_fingerprint')
            if (type(value.get('schema')) is not int or value['schema'] != 1 or not isinstance(slug, str)
                    or not SLUG.fullmatch(slug) or not sha(fingerprint)
                    or value.get('image') != image or not sha(value.get('coverage_sha256'))
                    or value.get('validation') != 'gph-v3-index-v1'):
                raise ValueError('invalid object graph identity')
            tiles = value.get('tiles')
            if not isinstance(tiles, dict) or not tiles:
                raise ValueError('empty object tile inventory')
            for path, tile in tiles.items():
                if (not TILE.fullmatch(path) or not isinstance(tile, dict)
                        or set(tile) != {'sha256', 'size'} or not sha(tile['sha256'])
                        or type(tile['size']) is not int or tile['size'] <= 0):
                    raise ValueError('invalid object tile declaration')
            hashes = {path: tile['sha256'] for path, tile in tiles.items()}
            if canonical_hash(hashes) != fingerprint:
                raise ValueError('graph fingerprint differs from tile inventory')
            identity = (slug, fingerprint)
            if identity in self.graphs:
                raise ValueError('duplicate object graph identity')
            self.graphs[identity] = value

    def lookup(self, slug, fingerprint, path):
        # An absent tile is a normal graph boundary, never an arbitrary R2 key.
        graph = self.graphs.get((slug, fingerprint))
        tile = graph['tiles'].get(path) if graph else None
        if tile is None:
            raise KeyError('tile outside trusted graph inventory')
        key = '/'.join(('navigation', 'graphs', slug, fingerprint, 'tiles', path))
        return dict(tile, key=key)
