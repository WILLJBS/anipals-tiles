"""Durable retirement of formerly active graphs, guarded by native FD leases."""
import errno
import fcntl
import json
import os
import re
import shutil
from contextlib import contextmanager
from pathlib import Path
from regional_storage import atomic_json, sync_dir


def identity(slug, fingerprint):
    if not isinstance(slug, str) or not re.fullmatch(r'[a-z0-9-]{1,120}', slug):
        raise ValueError('unsafe region slug')
    if not isinstance(fingerprint, str) or not re.fullmatch(r'[0-9a-f]{64}', fingerprint):
        raise ValueError('unsafe graph fingerprint')


def safe_path(path):
    if path.is_symlink() or path.resolve() != path:
        raise ValueError('symlinked graph storage is forbidden')
    return path


@contextmanager
def file_lock(path, operation):
    # O_NOFOLLOW protects lock identity even if a symlink appears after precheck.
    safe_path(path)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, operation)
        yield fd
    finally:
        # Do not explicitly LOCK_UN: inherited native FDs keep the same lease.
        os.close(fd)


def read_pointer(parent):
    active = safe_path(parent / 'active.json')
    if not active.exists():
        return None
    pointer = json.loads(active.read_text())
    identity(parent.name, pointer['fingerprint'])
    return pointer


def read_retired(parent):
    path = safe_path(parent / 'retired.json')
    queue = json.loads(path.read_text())['fingerprints'] if path.exists() else []
    if not isinstance(queue, list) or len(queue) != len(set(queue)):
        raise ValueError('invalid retirement queue')
    for fingerprint in queue:
        identity(parent.name, fingerprint)
    return queue


def region_parent(data_root, slug):
    root = Path(data_root).resolve()
    regions = safe_path(root / 'regions')
    parent = safe_path(regions / slug)
    parent.mkdir(parents=True, exist_ok=True)
    return parent


def activate(data_root, descriptor):
    """Caller proves native verification; immutable marker proves graph identity."""
    slug, fingerprint = descriptor['slug'], descriptor['fingerprint']
    identity(slug, fingerprint)
    parent = region_parent(data_root, slug)
    with file_lock(parent / '.activation.lock', fcntl.LOCK_EX):
        graph = safe_path(parent / fingerprint)
        marker = safe_path(graph / '.complete.json')
        if not marker.exists() or json.loads(marker.read_text()) != descriptor:
            raise ValueError('cannot activate an incomplete graph')
        pointer = read_pointer(parent)
        queue = read_retired(parent)
        if pointer and pointer['fingerprint'] != fingerprint:
            old = pointer['fingerprint']
            if old not in queue:
                queue.append(old)
            # Journal first: a crash before active switch is safe because GC
            # rechecks active under this same lock and never deletes that graph.
            atomic_json(parent / 'retired.json', {'fingerprints': queue})
        atomic_json(parent / 'active.json', {k: descriptor[k] for k in ('fingerprint', 'release')})


def collect_region(parent):
    safe_path(parent)
    identity(parent.name, '0' * 64)
    with file_lock(parent / '.activation.lock', fcntl.LOCK_EX):
        pointer = read_pointer(parent)
        queue = read_retired(parent)
        remaining = []
        for fingerprint in queue:
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
        return len(remaining)


def collect_retired(data_root):
    """Nonblocking leases: only durable retired entries, never arbitrary roots."""
    regions = safe_path(Path(data_root).resolve() / 'regions')
    if not regions.exists():
        return 0
    return sum(collect_region(path.parent) for path in sorted(regions.glob('*/retired.json')))
