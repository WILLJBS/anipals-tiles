"""Two-pass regional tar traversal, retaining one authenticated shard at a time."""
from contextlib import closing
import hashlib
from pathlib import Path
import re
import tarfile
import tempfile
import time
from migration_queue import UploadQueue, MAX_PENDING_BYTES, MAX_WORKERS

from regional_download import download_part
from regional_storage import PartStream, digest, require_capacity
from validate_tiles import header, validate_tile

TILE = re.compile(r'[012]/(?:[0-9]{3}/)*[0-9]{3}\.gph')


def verified_paths(plan, work, downloader, *, reserve_bytes=640*1024*1024, progress=lambda event: None, phase='inventory'):
    for i, part in enumerate(plan['parts']):
        path = work / ('part-%04d' % i)
        require_capacity(work, part['size'], reserve_bytes)
        started = time.monotonic()
        progress(dict(event='source_part_started', phase=phase, partIndex=i, partBytes=part['size']))
        downloader(path, part)
        if path.stat().st_size != part['size'] or digest(path) != part['sha256']:
            raise ValueError('release shard failed exact size/SHA256')
        progress(dict(event='source_part_verified', phase=phase, partIndex=i, partBytes=part['size'],
                      elapsedMs=round((time.monotonic()-started)*1000)))
        try:
            yield path
        finally:
            path.unlink(missing_ok=True)


def members(plan, work, downloader=download_part, *, reserve_bytes=640*1024*1024, progress=lambda event: None, phase='inventory'):
    """Yield each safe tile as one temporary file, preserving regional isolation."""
    seen = set()
    with tempfile.TemporaryDirectory(prefix='region-pass-', dir=work) as name:
        folder = Path(name)
        with closing(verified_paths(plan, folder, downloader, reserve_bytes=reserve_bytes, progress=progress, phase=phase)) as paths, PartStream(paths) as stream:
            with tarfile.open(fileobj=stream, mode='r|', ignore_zeros=True) as archive:
                for member in archive:
                    path = Path(member.name)
                    if path.is_absolute() or '..' in path.parts or not path.parts or path.parts[0] != 'tiles':
                        raise ValueError('unsafe release archive member')
                    if member.sparse is not None or not (member.isdir() or member.isfile()):
                        raise ValueError('release archive contains link or special member')
                    if member.isdir():
                        continue
                    relative = '/'.join(path.parts[1:])
                    if not TILE.fullmatch(relative) or relative in seen:
                        raise ValueError('duplicate or unsupported release file')
                    if not 272 <= member.size <= 512 * 1024 * 1024:
                        raise ValueError('tile outside bounded streaming size')
                    seen.add(relative)
                    target = folder / 'current.gph'
                    sha = hashlib.sha256(); size = 0
                    with archive.extractfile(member) as source, target.open('wb') as output:
                        for chunk in iter(lambda: source.read(1024 * 1024), b''):
                            size += len(chunk); sha.update(chunk); output.write(chunk)
                    if size != member.size:
                        raise ValueError('truncated archive tile')
                    try:
                        yield relative, target, dict(size=size, sha256=sha.hexdigest())
                    finally:
                        target.unlink(missing_ok=True)


def inventory(plan, work, downloader=download_part, *, progress=lambda event: None):
    tiles, headers = {}, {}
    total, started, last = 0, time.monotonic(), time.monotonic()
    for relative, path, item in members(plan, work, downloader, progress=progress):
        with path.open('rb') as stream:
            value = header(stream.read(272), item['size'])
        parts = Path(relative).with_suffix('').parts
        if value['graph'] >> 3 != int(''.join(parts[1:])) or value['graph'] & 7 != int(parts[0]):
            raise ValueError('tile path/header graphid mismatch')
        if value['graph'] in headers:
            raise ValueError('duplicate regional graph id')
        tiles[relative] = dict(item, graph=value['graph'])
        headers[value['graph']] = value
        total += item['size']
        if time.monotonic()-last >= 5:
            progress(dict(event='inventory_progress', tiles=len(tiles), tileBytes=total))
            last = time.monotonic()
    if not tiles:
        raise ValueError('empty regional graph')
    progress(dict(event='inventory_complete', tiles=len(tiles), tileBytes=total,
                  elapsedMs=round((time.monotonic()-started)*1000)))
    return tiles, headers


def migrate(plan, work, tiles, headers, upload, limit=None, downloader=download_part, *,
            workers=MAX_WORKERS, max_pending_bytes=MAX_PENDING_BYTES, progress=lambda event: None):
    count, external, validated_ms = 0, 0, 0
    # The existing 640 MiB reserve covers the current tile; pending owned files
    # require their own allowance even while a new source shard downloads.
    reserve = 640*1024*1024 + max_pending_bytes
    with UploadQueue(work, upload, workers=workers, max_bytes=max_pending_bytes, progress=progress) as queue:
        with closing(members(plan, work, downloader, reserve_bytes=reserve,
                             progress=progress, phase='migration')) as stream:
            for relative, path, actual in stream:
                expected = tiles.get(relative)
                if expected is None or actual != {key: expected[key] for key in ('size', 'sha256')}:
                    raise ValueError('second pass differs from authenticated inventory')
                started = time.monotonic()
                external += validate_tile(path, headers[expected['graph']], headers)
                validated_ms += round((time.monotonic()-started)*1000)
                queue.submit(relative, path, actual)
                count += 1
                if limit is not None and count >= limit:
                    break
        if limit is None and count != len(tiles):
            raise ValueError('incomplete regional migration')
    progress(dict(event='migration_structure_complete', tiles=count, validationMs=validated_ms))
    return dict(tiles=count, external_references=external)
