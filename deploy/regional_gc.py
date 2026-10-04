"""Durable retirement of formerly active graphs, guarded by native FD leases."""
import errno
import fcntl
import json
import os
import shutil
from pathlib import Path
from regional_storage import atomic_json, sync_dir
from regional_state import identity, safe_path, file_lock, read_retired, region_parent
from regional_ownership import read_pointer, read_control, assert_activation, write_active


def activate(data_root, descriptor, index_sha=None):
    """Caller proves native verification; immutable marker proves graph identity."""
    slug, fingerprint = descriptor['slug'], descriptor['fingerprint']
    identity(slug, fingerprint)
    parent = region_parent(data_root, slug)
    with file_lock(parent / '.activation.lock', fcntl.LOCK_EX):
        graph = safe_path(parent / fingerprint)
        marker = safe_path(graph / '.complete.json')
        if not marker.exists() or json.loads(marker.read_text()) != descriptor:
            raise ValueError('cannot activate an incomplete graph')
        control = assert_activation(parent, descriptor, index_sha)
        pointer = read_pointer(parent)
        queue = read_retired(parent)
        if pointer and pointer['fingerprint'] != fingerprint:
            old = pointer['fingerprint']
            if old not in queue:
                queue.append(old)
            # Journal first: a crash before active switch is safe because GC
            # rechecks active under this same lock and never deletes that graph.
            atomic_json(parent / 'retired.json', {'fingerprints': queue})
        selected = {k: descriptor[k] for k in ('fingerprint', 'release')}
        if control is None:
            atomic_json(parent / 'active.json', selected)
        else:
            write_active(parent, selected, control)


def collect_region(parent):
    safe_path(parent)
    identity(parent.name, '0' * 64)
    with file_lock(parent / '.activation.lock', fcntl.LOCK_EX):
        pointer = read_pointer(parent)
        queue = read_retired(parent)
        control = read_control(parent)
        retained = control['rollback']['fingerprint'] if control and control['rollback'] else None
        remaining = []
        for fingerprint in queue:
            if fingerprint == retained:
                remaining.append(fingerprint)
                continue
            if pointer and pointer['fingerprint'] == fingerprint:
                # Retirement journal survived a crash before the active switch.
                continue
            graph = safe_path(parent / fingerprint)
            trash = safe_path(parent / ('.retired-' + fingerprint))
            if graph.exists() and trash.exists():
                raise ValueError('conflicting retired graph and deletion tombstone')
            target = trash if trash.exists() else graph
            if not target.exists():
                continue  # deletion finished before queue update/fsync
            try:
                with file_lock(target / '.lease.lock', fcntl.LOCK_EX | fcntl.LOCK_NB):
                    if target == graph:
                        os.replace(graph, trash)
                        sync_dir(parent)
                    shutil.rmtree(trash)
                    sync_dir(parent)
            except OSError as error:
                if error.errno not in (errno.EAGAIN, errno.EWOULDBLOCK):
                    raise
                remaining.append(fingerprint)
        if remaining != queue:
            atomic_json(parent / 'retired.json', {'fingerprints': remaining})
        return len([fp for fp in remaining if fp != retained])


def collect_retired(data_root):
    """Nonblocking leases: only durable retired entries, never arbitrary roots."""
    regions = safe_path(Path(data_root).resolve() / 'regions')
    if not regions.exists():
        return 0
    return sum(collect_region(path.parent) for path in sorted(regions.glob('*/retired.json')))
