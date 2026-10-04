"""Read-only observations of an actual mounted runtime volume, never inferred graphs."""
import fcntl
import hashlib
import os
from pathlib import Path
from migration_contract import load_profile
from navigation_catalog_inputs import decode, require, SHA
from regional_objects import TILE
from regional_release import canonical_hash
from regional_state import safe_path, identity
from regional_ownership import read_control, read_pointer

SCHEMA = 'anipals-runtime-local-inventory-v1'


def read_json(path, maximum=65536):
    safe_path(path)
    with path.open('rb') as stream:
        raw = stream.read(maximum+1)
    require(0 < len(raw) <= maximum, 'VOLUME_METADATA_BOUNDS')
    return dict(sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw), value=decode(raw))


def selected_slugs(values):
    baseline = {r['slug'] for name in ('original61','gap3','additions129')
                for r in load_profile(name)['roster']['region']}
    require(isinstance(values, list) and all(isinstance(s, str) and s in baseline for s in values)
            and len(set(values)) == len(values), 'EXACT_REGISTERED_INVENTORY_SLUGS_REQUIRED')
    return sorted(values)


def observe(root, slug, image):
    parent = safe_path(root/'regions'/slug)
    # Existing runtime activation locks only: inspection must not create files.
    lock = safe_path(parent/'.activation.lock')
    fd = os.open(lock, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        fcntl.flock(fd, fcntl.LOCK_SH)
        control = read_control(parent)
        pointer = read_pointer(parent)
        wanted = control['rollback'] if control and control['rollback'] else None
        require(wanted is not None or pointer is not None, 'NO_OBSERVED_LOCAL_GENERATION')
        fp = wanted['fingerprint'] if wanted else pointer['fingerprint']
        identity(slug, fp)
        folder = safe_path(parent/fp)
        marker = read_json(folder/'.complete.json'); descriptor = marker['value']
        require(isinstance(descriptor, dict) and descriptor.get('slug') == slug
                and descriptor.get('fingerprint') == fp and descriptor.get('storage', 'local') == 'local'
                and descriptor.get('tile_dir') == 'regions/%s/%s/tiles' % (slug, fp)
                and type(descriptor.get('tile_count')) is int and descriptor['tile_count'] > 0,
                'LOCAL_MARKER_IDENTITY_INVALID')
        meta = read_json(folder/'.identity.json'); value = meta['value']
        require(isinstance(value, dict) and value.get('fingerprint') == fp and value.get('image') == image
                and type(value.get('bytes')) is int and value['bytes'] > 0, 'LOCAL_IMAGE_OR_IDENTITY_INVALID')
        tiles = safe_path(folder/'tiles'); require(tiles.is_dir(), 'LOCAL_TILES_MISSING')
        paths = {}
        for directory, dirs, files in os.walk(tiles, followlinks=False):
            for name in dirs:
                safe_path(Path(directory)/name)
            for name in files:
                path = safe_path(Path(directory)/name); relative = path.relative_to(tiles).as_posix()
                require(path.is_file() and TILE.fullmatch(relative), 'UNSAFE_LOCAL_TILE_PATH')
                size = path.stat().st_size; require(size > 0, 'EMPTY_LOCAL_TILE')
                digest = hashlib.sha256()
                with path.open('rb') as stream:
                    for chunk in iter(lambda: stream.read(1024*1024), b''):
                        digest.update(chunk)
                require(path.stat().st_size == size, 'LOCAL_TILE_CHANGED_DURING_READ')
                paths[relative] = dict(size=size, sha256=digest.hexdigest())
        require(len(paths) == descriptor['tile_count'] and sum(item['size'] for item in paths.values()) == value['bytes'],
                'LOCAL_ROLLBACK_INCOMPLETE')
        state = read_json(parent/('ownership.json' if control else 'active.json'))
        return dict(slug=slug, fingerprint=fp, marker=marker, identity=meta,
                    runtime_state=state, tile_inventory_sha256=canonical_hash(paths),
                    graph_fingerprint=canonical_hash({name: item['sha256'] for name, item in paths.items()}),
                    tile_count=len(paths), tile_bytes=sum(item['size'] for item in paths.values()))
    finally:
        os.close(fd)


def observed_state(root, slug):
    parent = safe_path(root/'regions'/slug)
    if not parent.exists():
        return None
    paths = [parent/'ownership.json', parent/'active.json']
    if not any(p.exists() for p in paths):
        return None
    fd = os.open(safe_path(parent/'.activation.lock'), os.O_RDONLY | os.O_NOFOLLOW)
    try:
        fcntl.flock(fd, fcntl.LOCK_SH)
        control = read_control(parent); read_pointer(parent)
        return read_json(paths[0] if control else paths[1])
    finally:
        os.close(fd)


def capture(root, slugs, image, target_identity_sha256):
    require(isinstance(target_identity_sha256, str) and SHA.fullmatch(target_identity_sha256), 'TARGET_IDENTITY_SHA_REQUIRED')
    root = Path(root).absolute(); safe_path(root)
    require(root.is_dir(), 'MOUNTED_RUNTIME_VOLUME_REQUIRED')
    selected = selected_slugs(slugs)
    baseline = {r['slug'] for r in load_profile('original61')['roster']['region']}
    rows = [observe(root, slug, image) for slug in selected if slug in baseline]
    local = {row['slug']: row for row in rows}
    states = {slug: local[slug]['runtime_state'] if slug in local else observed_state(root, slug) for slug in selected}
    stat = root.stat()
    return dict(schema=SCHEMA, target_identity_sha256=target_identity_sha256,
                volume=dict(device=stat.st_dev, inode=stat.st_ino), image=image, regions=rows,
                selected_regions=selected, runtime_states=states,
                observed_filesystem=True, tile_bytes_hashed=True, native_rollback_verified=False)
