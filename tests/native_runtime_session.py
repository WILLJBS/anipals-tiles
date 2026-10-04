"""Isolated runtime state-machine acceptance; native I/O is supplied by the runner."""
import fcntl
from pathlib import Path
from native_r2_acceptance import require, route_pair
from regional_catalog import Catalog
from regional_download import activate_region
from regional_gc import collect_retired
from regional_ownership import checked_marker, read_control, read_pointer, rollback
from regional_router import Router
from regional_state import file_lock


def signature(result):
    trip = result['trip']
    return dict(units=trip['units'], length=trip['summary']['length'],
                shapes=[leg['shape'] for leg in trip['legs']])


class RecordingEngine:
    def __init__(self, engine):
        self.engine, self.routes = engine, []

    def request(self, region, action, payload, **kwargs):
        result = self.engine.request(region, action, payload, **kwargs)
        if action == 'route':
            self.routes.append(signature(result))
        return result


def rejected(action, code):
    try:
        action()
    except ValueError:
        return
    raise ValueError(code)


def selected_route(router, payload, storage, fingerprint, expected):
    result, region = router.route(payload)
    require(region.get('storage', 'local') == storage
            and region['fingerprint'] == fingerprint, 'ROUTE_USED_WRONG_STORAGE_OWNER')
    require(signature(result) == expected, 'ACTIVE_ROUTE_DIFFERS_FROM_VERIFIED_NATIVE_SHAPE')


def exercise(composite, root, engine, payload, probes, counter, bridge):
    """Mutate only the caller's verified isolated volume; no completion until restart."""
    require(composite.schema == 2 and len(composite.rows) == 1, 'SINGLE_SCHEMA2_ACCEPTANCE_REQUIRED')
    slug = next(iter(composite.rows))
    owner = composite.ownership[slug]
    require(owner['selected']['storage'] == 'r2' and owner['rollback'] is not None,
            'REAL_LOCAL_ROLLBACK_REQUIRED')
    parent = Path(root)/'regions'/slug
    local = checked_marker(parent, owner['rollback'])
    require(read_control(parent) is None and read_pointer(parent)['fingerprint'] == local['fingerprint'],
            'FRESH_ISOLATED_LOCAL_VOLUME_REQUIRED')
    engine = RecordingEngine(engine)
    router = Router(Catalog(root, composite.coverage), engine, probes)
    local_proof = router.verify(slug, local['fingerprint'])
    composite.install_ownership(root)
    remote = composite.prepare(root, slug)
    region = router.catalog.candidate(slug, remote['fingerprint'])
    require(engine.request(region, 'status', {}).get('version') == '3.3.0', 'NATIVE_ABI_DIFFERS')
    require(counter.snapshot()[1:] == (0, 0), 'STATUS_OR_LOCAL_VERIFY_WARMED_R2')
    records = route_pair(engine, region, payload, counter, bridge)
    expected = engine.routes[-1]
    proof = router.verify(slug, remote['fingerprint'])
    activate_region(root, remote, proof, composite.digest)
    selected_route(router, payload, 'r2', remote['fingerprint'], expected)
    rejected(lambda: activate_region(root, local, local_proof), 'STALE_LOCAL_OVERWROTE_R2')
    require(read_pointer(parent)['fingerprint'] == remote['fingerprint'], 'STALE_LOCAL_CHANGED_POINTER')
    collect_retired(root)
    require((parent/local['fingerprint']).is_dir(), 'GC_REMOVED_RETAINED_LOCAL')
    remote_path = parent/remote['fingerprint']
    with file_lock(remote_path/'.lease.lock', fcntl.LOCK_SH):
        rollback(root, slug, composite.digest, local['fingerprint'], router.verify(slug, local['fingerprint']))
        selected_route(router, payload, 'local', local['fingerprint'], expected)
        rejected(lambda: activate_region(root, remote, proof, composite.digest), 'STALE_R2_OVERWROTE_ROLLBACK')
        require(collect_retired(root) == 1 and remote_path.is_dir(), 'GC_IGNORED_ACTIVE_NATIVE_LEASE')
    require(collect_retired(root) == 0 and not remote_path.exists(), 'GC_DID_NOT_RELEASE_RETIRED_R2')
    require((parent/local['fingerprint']).is_dir()
            and read_control(parent)['mode'] == 'rollback', 'ROLLBACK_OR_RETENTION_LOST')
    return dict(slug=slug, local_fingerprint=local['fingerprint'], remote_fingerprint=remote['fingerprint'],
                signature=expected, records=records, index_sha256=composite.digest,
                restart_verified=False, production_activated=False)


def check_restart(composite, root, engine, payload, probes, expected):
    """Called in a separate interpreter, with no R2 bridge or R2 credentials."""
    slug = expected['slug']; parent = Path(root)/'regions'/slug
    before = read_control(parent)
    require(before is not None and before['mode'] == 'rollback'
            and before['index_sha256'] == composite.digest, 'RESTART_LOST_DURABLE_ROLLBACK')
    composite.install_ownership(root)
    require(read_control(parent) == before, 'RESTART_RESET_EXPLICIT_ROLLBACK')
    router = Router(Catalog(root, composite.coverage), engine, probes)
    selected_route(router, payload, 'local', expected['local_fingerprint'], expected['signature'])
    require(collect_retired(root) == 0
            and (parent/expected['local_fingerprint']).is_dir(), 'RESTART_GC_REMOVED_ROLLBACK')
    return dict(index_sha256=composite.digest, local_fingerprint=expected['local_fingerprint'],
                restart_verified=True, production_activated=False)
