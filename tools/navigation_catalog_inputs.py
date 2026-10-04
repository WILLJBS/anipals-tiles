"""Bounded immutable inputs for offline navigation catalog review (no network)."""
import hashlib
import json
import math
from pathlib import Path
import re

SHA = re.compile('[0-9a-f]{64}')
REVISION = re.compile('[0-9a-f]{40}')


def require(ok, code):
    if not ok:
        raise ValueError(code)


def exact(value, keys, code):
    require(isinstance(value, dict) and set(value) == set(keys.split()), code)


def decode(raw):
    def pairs(rows):
        result = {}
        for key, value in rows:
            require(key not in result, 'DUPLICATE_JSON_KEY')
            result[key] = value
        return result
    def invalid(_):
        raise ValueError('NONFINITE_JSON_NUMBER')
    def finite(text):
        value = float(text)
        require(math.isfinite(value), 'NONFINITE_JSON_NUMBER')
        return value
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid, parse_float=finite)


def checked_bytes(raw, digest, size, maximum):
    require(isinstance(digest, str) and SHA.fullmatch(digest)
            and type(size) is int and 0 < size <= maximum, 'INPUT_DESCRIPTOR_BOUNDS')
    require(type(raw) is bytes and len(raw) == size
            and hashlib.sha256(raw).hexdigest() == digest, 'INPUT_BYTES_SHA_MISMATCH')
    return raw


def descriptor(value, maximum, key=None):
    exact(value, 'key sha256 bytes', 'EXACT_DESCRIPTOR_REQUIRED')
    digest, size = value['sha256'], value['bytes']
    require(isinstance(digest, str) and SHA.fullmatch(digest)
            and type(size) is int and 0 < size <= maximum, 'INPUT_DESCRIPTOR_BOUNDS')
    expected = 'archive/sha256/%s/%s' % (digest[:2], digest) if key is None else key
    require(value['key'] == expected, 'INPUT_KEY_SUBSTITUTED')
    return value


class LocalObjects:
    """Explicit complete byte mirror, addressed by SHA filename, never arbitrary paths."""
    def __init__(self, root):
        self.root = Path(root).resolve(strict=True)
        require(self.root.is_dir(), 'OBJECT_DIRECTORY_REQUIRED')

    def read(self, ref, maximum, key=None):
        descriptor(ref, maximum, key)
        path = self.root / ref['sha256']
        require(not path.is_symlink() and path.is_file(), 'MISSING_OR_UNSAFE_INPUT_OBJECT')
        with path.open('rb') as stream:
            raw = stream.read(ref['bytes'] + 1)
        return checked_bytes(raw, ref['sha256'], ref['bytes'], maximum)
