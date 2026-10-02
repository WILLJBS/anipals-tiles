#!/usr/bin/env python3
"""Extract validated archive paths to staging; expose each tile by atomic rename."""
import fcntl
import hashlib
import os
import sys
import tarfile
from pathlib import Path
archive, stage, target = map(Path, sys.argv[1:])
with tarfile.open(archive) as tar:
    members = tar.getmembers()
    for member in members:
        parts = Path(member.name).parts
        if not parts or parts[0] != 'tiles' or '..' in parts or Path(member.name).is_absolute():
            raise ValueError('unsafe archive path: ' + member.name)
        if not member.isdir() and not member.isfile():
            raise ValueError('links/devices are not tile files')
    tar.extractall(stage, members=members)
files = [p for p in (stage / 'tiles').rglob('*') if p.is_file()]
# Shared GraphIds from independent regional graphs are not composable. Serialize
# preflight AND commit across all four downloader workers; unlocked checks race.
with (target / '.install.lock').open('a') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    for source in files:
        destination = target / source.relative_to(stage / 'tiles')
        if destination.exists() and source.suffix == '.gph':
            if hashlib.sha256(source.read_bytes()).digest() != hashlib.sha256(destination.read_bytes()).digest():
                raise ValueError('incompatible overlapping regional tile: ' + str(destination))
    for source in files:
        destination = target / source.relative_to(stage / 'tiles')
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source, destination)
