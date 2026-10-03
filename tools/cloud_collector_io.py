"""Private immutable blobs and write-ahead source-byte accounting for one CI run scope."""
import hashlib
import json
from pathlib import Path
import re
import threading
from private_archive_verify import read_verified, transient
import time
from storage_errors import error_details


def fingerprint(path):
    digest = hashlib.sha256(); size = 0
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            digest.update(block); size += len(block)
    sha = digest.hexdigest()
    return {'key': f'archive/sha256/{sha[:2]}/{sha}', 'sha256': sha, 'bytes': size}


def retry(operation):
    for attempt in range(3):
        try: return operation()
        except Exception as error:
            if not transient(error) or attempt == 2 or getattr(error, 'multipart_abort_failed', False): raise
            time.sleep(2)


class PrivateStore:
    def __init__(self, client, bucket, uploader=None):
        self.client, self.bucket, self.uploader = client, bucket, uploader

    def json(self, key, sha, size):
        return json.loads(read_verified(self.client, self.bucket, key, sha, size, capture=True))

    def fetch(self, descriptor, path):
        for attempt in range(3):
            try: return self._fetch(descriptor, path)
            except Exception as error:
                if not transient(error) or attempt == 2: raise
                time.sleep(2)

    def _fetch(self, descriptor, path):
        if path.exists() and fingerprint(path)['sha256'] == descriptor['sha256'] and path.stat().st_size == descriptor['bytes']:
            return
        path.parent.mkdir(parents=True, exist_ok=True); temp = path.with_suffix(path.suffix+'.download')
        response = self.client.get_object(Bucket=self.bucket, Key=descriptor['key']); body = response['Body']
        digest = hashlib.sha256(); size = 0
        try:
            with temp.open('wb') as target:
                for block in iter(lambda: body.read(1024*1024), b''):
                    size += len(block)
                    if size > descriptor['bytes']: raise ValueError('PRIVATE_BLOB_SIZE_MISMATCH')
                    digest.update(block); target.write(block)
            if size != descriptor['bytes'] or digest.hexdigest() != descriptor['sha256']:
                raise ValueError('PRIVATE_BLOB_SHA_MISMATCH')
            temp.replace(path)
        finally:
            body.close(); temp.unlink(missing_ok=True)

    def put(self, path):
        desc = fingerprint(path)
        retry(lambda: self.uploader(self.client, self.bucket, desc['key'], path, desc['bytes'], desc['sha256'],
                      {'ContentType': 'application/octet-stream', 'CacheControl': 'private, no-store'}))
        read_verified(self.client, self.bucket, desc['key'], desc['sha256'], desc['bytes'])
        return desc

    def save_json(self, prefix, value):
        raw = json.dumps(value, sort_keys=True, separators=(',', ':')).encode()
        sha = hashlib.sha256(raw).hexdigest(); key = prefix+sha+'.json'
        try:
            retry(lambda: self.client.put_object(Bucket=self.bucket, Key=key, Body=raw, ContentLength=len(raw),
                                   IfNoneMatch='*', ContentType='application/json', CacheControl='private, no-store'))
        except Exception as error:
            code, status = error_details(error)
            if code not in ('412', '409', 'PreconditionFailed', 'ConditionalRequestConflict') and status not in (409, 412):
                raise
        read_verified(self.client, self.bucket, key, sha, len(raw))
        return {'key': key, 'sha256': sha, 'bytes': len(raw)}

    def list(self, prefix):
        token = None
        while True:
            args = {'Bucket': self.bucket, 'Prefix': prefix}
            if token: args['ContinuationToken'] = token
            result = retry(lambda: self.client.list_objects_v2(**args))
            yield from result.get('Contents', [])
            if not result.get('IsTruncated'): break
            token = result['NextContinuationToken']


class Journal:
    def __init__(self, store, spec_sha, budget, prior=0):
        if not re.fullmatch('[a-f0-9]{64}', spec_sha): raise ValueError('SPEC_SHA_REQUIRED')
        self.store, self.budget = store, budget
        self.prefix = f'archive/collector/{spec_sha}/'
        self.seq, self.spent, self.pending = 0, prior, {}
        self.ranges, self.files = {}, {}; self.lock = threading.RLock()
        snapshots = sorted(store.list(self.prefix+'snapshots/'), key=lambda x: x['Key'])
        if len({r['Key'].rsplit('/', 1)[1].split('-')[0] for r in snapshots}) != len(snapshots):
            raise ValueError('CONCURRENT_CHECKPOINT_SNAPSHOTS')
        if snapshots:
            value = self.read(snapshots[-1]); self.seq = value['seq']; self.spent = value['spent']
            self.pending, self.ranges, self.files = value['pending'], value['ranges'], value['files']
        seen_sequences = set()
        for row in sorted(store.list(self.prefix+'journal/'), key=lambda x: x['Key']):
            seq = int(row['Key'].rsplit('/', 1)[1].split('-')[0])
            if seq in seen_sequences: raise ValueError('CONCURRENT_CHECKPOINT_WRITERS')
            seen_sequences.add(seq)
            if seq <= self.seq: continue
            if seq != self.seq+1: raise ValueError('CHECKPOINT_JOURNAL_SEQUENCE_GAP')
            self.reduce(self.read(row)); self.seq = seq
        # Any unresolved reservation remains charged at its full retry bound.
        if self.used > self.budget: raise ValueError('CHECKPOINT_BUDGET_EXCEEDED')

    def read(self, row):
        sha = row['Key'].rsplit('-', 1)[-1].removesuffix('.json')
        return self.store.json(row['Key'], sha, row['Size'])

    @property
    def used(self): return self.spent+sum(self.pending.values())

    def reduce(self, event):
        if event['kind'] == 'reserve': self.pending[event['id']] = event['upper']
        elif event['kind'] == 'settle':
            upper = self.pending.pop(event['id'])
            if not 0 <= event['actual'] <= upper: raise ValueError('INVALID_SOURCE_CHARGE')
            self.spent += event['actual']
            if event.get('range'): self.ranges[event['range']['cacheKey']] = event['range']
        elif event['kind'] == 'file': self.files[event['name']] = event['descriptor']
        else: raise ValueError('UNKNOWN_CHECKPOINT_EVENT')

    def append(self, event):
        self.store.save_json(self.prefix+f'journal/{self.seq+1:012d}-', event)
        self.reduce(event); self.seq += 1

    def reserve(self, upper):
        with self.lock:
            if upper > self.budget-self.used: raise ValueError('SOURCE_DOWNLOAD_BUDGET_EXHAUSTED')
            key = str(self.seq+1); self.append({'kind': 'reserve', 'id': key, 'upper': upper}); return key

    def settle(self, key, actual, cached=None):
        with self.lock: self.append({'kind': 'settle', 'id': key, 'actual': actual, 'range': cached})

    def file(self, path):
        with self.lock:
            desc = self.store.put(path); self.append({'kind': 'file', 'name': path.name, 'descriptor': desc})
            self.snapshot(); return desc

    def snapshot(self):
        return self.store.save_json(self.prefix+f'snapshots/{self.seq:012d}-', {
            'seq': self.seq, 'spent': self.spent, 'pending': self.pending, 'ranges': self.ranges, 'files': self.files})
