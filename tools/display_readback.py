"""Bounded, identity-pinned R2 Range reads with one complete ordered SHA gate."""
import hashlib
import re
import time
from private_archive_verify import transient

RANGE_BYTES = 64 * 1024 * 1024
READ_BYTES = 1024 * 1024
SAFE_REASONS = frozenset(('REMOTE_SIZE_MISMATCH', 'REMOTE_SHA_MISMATCH',
    'REMOTE_METADATA_MISMATCH', 'REMOTE_ETAG_MISSING', 'REMOTE_IDENTITY_MISMATCH',
    'REMOTE_RANGE_MISMATCH', 'REMOTE_RANGE_TRUNCATED', 'INVALID_READBACK_IDENTITY'))


class TruncatedRange(OSError):
    """A premature EOF may be retried, unlike invalid object/range metadata."""


def identity(response, size, sha256, etag=None):
    if type(response.get('ContentLength')) is not int or response['ContentLength'] != size:
        raise ValueError('REMOTE_SIZE_MISMATCH')
    if response.get('Metadata', {}).get('sha256') != sha256:
        raise ValueError('REMOTE_METADATA_MISMATCH')
    actual = response.get('ETag')
    if (not isinstance(actual, str) or not re.fullmatch(r'"[^"\x00-\x20]+"', actual)):
        raise ValueError('REMOTE_ETAG_MISSING')
    if etag is not None and actual != etag:
        raise ValueError('REMOTE_IDENTITY_MISMATCH')
    if response.get('ContentEncoding', 'identity') != 'identity':
        raise ValueError('REMOTE_IDENTITY_MISMATCH')
    return actual


def head(client, bucket, key, sleep, etag=None):
    args = {'Bucket': bucket, 'Key': key}
    if etag is not None:
        args['IfMatch'] = etag
    for attempt in range(3):
        try:
            return client.head_object(**args)
        except Exception as error:
            if not transient(error) or attempt == 2:
                raise
            sleep(2)


def consume_range(client, bucket, key, size, sha256, etag, start, end, digest, sleep, progress):
    expected = end - start + 1
    for attempt in range(3):
        body = None
        try:
            response = client.get_object(Bucket=bucket, Key=key, IfMatch=etag,
                                         Range=f'bytes={start}-{end}')
            body = response['Body']
            identity(response, expected, sha256, etag)
            if (response.get('ResponseMetadata', {}).get('HTTPStatusCode') != 206
                    or response.get('ContentRange') != f'bytes {start}-{end}/{size}'):
                raise ValueError('REMOTE_RANGE_MISMATCH')
            # Failed streams must never contribute a partial prefix twice.
            candidate, received = digest.copy(), 0
            while True:
                chunk = body.read(min(READ_BYTES, expected - received + 1))
                if not chunk:
                    break
                received += len(chunk)
                if received > expected:
                    raise ValueError('REMOTE_SIZE_MISMATCH')
                candidate.update(chunk)
            if received != expected:
                raise TruncatedRange('REMOTE_RANGE_TRUNCATED')
            return candidate
        except Exception as error:
            if not transient(error) or attempt == 2:
                raise
            progress({'event': 'r2_readback_retry', 'verifiedBytes': start,
                      'totalBytes': size, 'rangeStart': start, 'rangeEnd': end,
                      'nextAttempt': attempt + 2, 'complete': False})
            sleep(2)
        finally:
            if body is not None:
                body.close()


def verify_remote(client, bucket, key, size, sha256, *, range_bytes=RANGE_BYTES,
                  progress=lambda value: None, sleep=time.sleep):
    if (type(size) is not int or size <= 0 or type(range_bytes) is not int
            or not 1 <= range_bytes <= RANGE_BYTES
            or not isinstance(sha256, str) or not re.fullmatch(r'[a-f0-9]{64}', sha256)):
        raise ValueError('INVALID_READBACK_IDENTITY')
    etag = identity(head(client, bucket, key, sleep), size, sha256)
    digest = hashlib.sha256()
    progress({'event': 'r2_readback_started', 'verifiedBytes': 0,
              'totalBytes': size, 'complete': False})
    for start in range(0, size, range_bytes):
        end = min(size, start + range_bytes) - 1
        digest = consume_range(client, bucket, key, size, sha256, etag,
                               start, end, digest, sleep, progress)
        progress({'event': 'r2_readback_progress', 'verifiedBytes': end + 1,
                  'totalBytes': size, 'complete': False})
    if digest.hexdigest() != sha256:
        raise ValueError('REMOTE_SHA_MISMATCH')
    # Detect deletion/replacement or metadata drift after the last byte read.
    identity(head(client, bucket, key, sleep, etag), size, sha256, etag)
    return {'key': key, 'bytes': size, 'sha256': sha256, 'etag': etag[1:-1]}
