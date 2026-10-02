"""Durable, bounded storage primitives for isolated native regional graphs."""
import hashlib
import io
import json
import os
import shutil
import tarfile
from pathlib import Path


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    with temporary.open('w') as output:
        json.dump(value, output, sort_keys=True)
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)
    sync_dir(path.parent)


def digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def require_capacity(root, required, reserve):
    free = shutil.disk_usage(root).free
    if free < required + reserve:
        raise OSError('insufficient disk: free=%d required=%d reserve=%d' % (free, required, reserve))


class PartStream(io.RawIOBase):
    """One open part at a time; tar reads across split boundaries without a tar copy."""
    def __init__(self, paths):
        self.paths = iter(paths)
        self.source = None

    def readable(self):
        return True

    def read(self, size=-1):
        if size < 0:
            raise ValueError('unbounded tar read is forbidden')
        chunks = []
        remaining = size
        while remaining:
            if self.source is None:
                path = next(self.paths, None)
                if path is None:
                    break
                self.source = Path(path).open('rb')
            chunk = self.source.read(remaining)
            if not chunk:
                self.source.close()
                self.source = None
                continue
            chunks.append(chunk)
            remaining -= len(chunk)
        return b''.join(chunks)

    def close(self):
        if self.source:
            self.source.close()
        super().close()


def extract_parts(parts, target, archive_bytes):
    """Only uncompressed regular files/dirs inside tiles/. No links or sparse data."""
    target = Path(target)
    target.mkdir(parents=True, exist_ok=True)
    written = 0
    seen = set()
    with PartStream(parts) as stream, tarfile.open(fileobj=stream, mode='r|') as archive:
        for member in archive:
            name = Path(member.name)
            if name.is_absolute() or '..' in name.parts or not name.parts or name.parts[0] != 'tiles':
                raise ValueError('unsafe archive member: ' + member.name)
            if member.sparse is not None or not (member.isdir() or member.isfile()):
                raise ValueError('links/sparse/special archive members forbidden')
            destination = target.joinpath(*name.parts[1:])
            if member.isdir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            if destination in seen or len(name.parts) == 1:
                raise ValueError('duplicate/invalid archive file: ' + member.name)
            seen.add(destination)
            written += member.size
            if written > archive_bytes:
                raise ValueError('extracted bytes exceed verified uncompressed archive size')
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(member) as source, destination.open('xb') as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
                output.flush()
                os.fsync(output.fileno())
        for directory, _, _ in os.walk(target, topdown=False):
            sync_dir(directory)
    return written
