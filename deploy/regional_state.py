"""Filesystem identities and locks shared by ownership, activation and GC."""
import fcntl
import json
import os
import re
from contextlib import contextmanager
from pathlib import Path


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


def read_legacy_pointer(parent):
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
    identity(slug, '0' * 64)
    root = Path(data_root).resolve()
    regions = safe_path(root / 'regions')
    parent = safe_path(regions / slug)
    parent.mkdir(parents=True, exist_ok=True)
    return parent
