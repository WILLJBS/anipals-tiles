"""One durable storage owner and an explicitly retained local rollback per region."""
import fcntl
import json
from pathlib import Path
from contextlib import ExitStack
from regional_release import canonical_hash, sha
from regional_storage import atomic_json
from regional_state import safe_path, identity, region_parent, file_lock, read_retired, read_legacy_pointer


def graph_identity(value, local_only=False):
    if (not isinstance(value, dict) or set(value) != {'storage', 'fingerprint'}
            or value['storage'] not in (('local',) if local_only else ('local', 'r2'))
            or not sha(value['fingerprint'])):
        raise ValueError('invalid storage ownership graph identity')
    return value


def descriptor_identity(descriptor):
    return dict(storage=descriptor.get('storage', 'local'), fingerprint=descriptor['fingerprint'])


def validate_ownership(value, base, remote_rows, image):
    if not sha(value.get('catalog_sha256')):
        raise ValueError('reviewed source catalog SHA required')
    previous = value.get('previous_index_sha256')
    if 'previous_index_sha256' not in value or previous is not None and not sha(previous):
        raise ValueError('invalid previous ownership index SHA')
    owners = value.get('storage_ownership')
    baseline = {f['properties']['slug']: f for f in base['features']}
    if not isinstance(owners, dict) or set(owners) != set(baseline) | set(remote_rows):
        raise ValueError('exact coverage storage ownership required')
    for slug, owner in owners.items():
        if not isinstance(owner, dict) or set(owner) != {'selected', 'rollback'}:
            raise ValueError('exact selected and rollback ownership required')
        selected = graph_identity(owner['selected'])
        rollback = owner['rollback']
        if rollback is not None:
            graph_identity(rollback, local_only=True)
        if selected['storage'] == 'r2':
            row = remote_rows.get(slug)
            if row is None or selected['fingerprint'] != canonical_hash(dict(manifest_sha256=row['manifest_sha256'], image=image)):
                raise ValueError('remote owner differs from exact manifest identity')
            if slug in baseline and rollback is None:
                raise ValueError('local replacement requires retained local rollback')
        elif slug not in baseline or slug in remote_rows:
            raise ValueError('local owner must be an unreplaced baseline region')
        if slug not in baseline and rollback is not None:
            raise ValueError('remote addition cannot claim a baseline rollback')
    return owners


def read_control(parent):
    path = safe_path(parent/'ownership.json')
    if not path.exists():
        return None
    value = json.loads(path.read_text())
    if (not isinstance(value, dict) or set(value) != {'schema', 'index_sha256', 'selected', 'rollback', 'mode', 'active'}
            or type(value.get('schema')) is not int or value['schema'] != 1 or not sha(value.get('index_sha256'))
            or value.get('mode') not in ('selected', 'rollback')):
        raise ValueError('invalid durable ownership record')
    graph_identity(value['selected'])
    if value['rollback'] is not None:
        graph_identity(value['rollback'], local_only=True)
    if value['mode'] == 'rollback' and value['rollback'] is None:
        raise ValueError('rollback mode has no retained graph')
    active = value['active']
    if active is not None:
        if (not isinstance(active, dict) or set(active) != {'fingerprint', 'release'}
                or not isinstance(active['release'], str) or not active['release']):
            raise ValueError('invalid ownership active pointer')
        identity(parent.name, active['fingerprint'])
    return value


def read_pointer(parent):
    control = read_control(parent)
    return control['active'] if control is not None else read_legacy_pointer(parent)


def target(control):
    return control['rollback'] if control['mode'] == 'rollback' else control['selected']


def checked_marker(parent, wanted):
    marker = safe_path(parent/wanted['fingerprint']/'.complete.json')
    descriptor = json.loads(marker.read_text())
    if descriptor['slug'] != parent.name or descriptor_identity(descriptor) != wanted:
        raise ValueError('ownership marker differs from retained identity')
    tiles = safe_path(parent/wanted['fingerprint']/'tiles')
    if not tiles.is_dir():
        raise ValueError('ownership graph is incomplete')
    return descriptor


def prepared_control(parent, owner, digest, previous):
    current = read_control(parent)
    if owner['rollback'] is not None:
        checked_marker(parent, owner['rollback'])
    if owner['selected']['storage'] == 'local':
        checked_marker(parent, owner['selected'])
    if current and current['index_sha256'] == digest:
        if any(current[k] != owner[k] for k in ('selected', 'rollback')):
            raise ValueError('same index changed immutable ownership')
        return None  # Marker checks still run; preserve explicit rollback mode.
    if (current is None and previous is not None and read_pointer(parent) is not None
            or current is not None and current['index_sha256'] != previous):
        raise ValueError('ownership previous index compare-and-swap failed')
    active = read_pointer(parent)
    if active and current is None and owner['rollback'] is not None and active['fingerprint'] != owner['rollback']['fingerprint']:
        raise ValueError('active local graph differs from reviewed rollback')
    return dict(schema=1, index_sha256=digest, **owner, mode='selected', active=active)


def install_many(root, owners, digest, previous):
    # Validate every retained graph before mutating any owner. Hold the same
    # activation locks used by GC/workers until all per-region records persist.
    with ExitStack() as locks:
        prepared = []
        for slug, owner in sorted(owners.items()):
            parent = region_parent(root, slug)
            locks.enter_context(file_lock(parent/'.activation.lock', fcntl.LOCK_EX))
            prepared.append((parent, prepared_control(parent, owner, digest, previous)))
        for parent, value in prepared:
            if value is not None:
                atomic_json(parent/'ownership.json', value)


def install(root, slug, owner, digest, previous):
    install_many(root, {slug: owner}, digest, previous)


def assert_activation(parent, descriptor, index_sha=None):
    control = read_control(parent)
    if control is None:
        if index_sha is not None:
            raise ValueError('owned activation has no installed index')
        return None
    if descriptor_identity(descriptor) != target(control):
        raise ValueError('stale worker cannot replace selected storage owner')
    if descriptor.get('storage') == 'r2' and index_sha != control['index_sha256']:
        raise ValueError('stale remote worker has different ownership index')
    return control


def write_active(parent, pointer, control):
    atomic_json(parent/'ownership.json', dict(control, active=pointer))


def allows(root, descriptor):
    parent = Path(root).resolve()/'regions'/descriptor['slug']
    control = read_control(parent)
    return control is None or descriptor_identity(descriptor) == target(control)


def local_allowed(root, plan):
    return allows(root, dict(slug=plan['slug'], fingerprint=plan['fingerprint']))


def rollback(root, slug, digest, fingerprint, verification):
    """Native verification precedes one atomic owner+pointer rollback switch."""
    parent = region_parent(root, slug)
    with file_lock(parent/'.activation.lock', fcntl.LOCK_EX):
        control = read_control(parent)
        if (control is None or control['index_sha256'] != digest
                or control['rollback'] != dict(storage='local', fingerprint=fingerprint)
                or not isinstance(verification, dict) or verification.get('verified') is not True
                or verification.get('slug') != slug or verification.get('fingerprint') != fingerprint):
            raise ValueError('explicit verified rollback identity required')
        descriptor = checked_marker(parent, control['rollback'])
        active = control['active']; retired = read_retired(parent)
        if active and active['fingerprint'] != fingerprint and active['fingerprint'] not in retired:
            retired.append(active['fingerprint'])
            atomic_json(parent/'retired.json', dict(fingerprints=retired))
        pointer = {k: descriptor[k] for k in ('fingerprint', 'release')}
        write_active(parent, pointer, dict(control, mode='rollback'))
        return descriptor
