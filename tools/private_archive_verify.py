#!/usr/bin/env python3
"""Verify uploaded private sources; publish receipts only after complete GETs."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import time
from storage_errors import error_details

ROOT = Path(__file__).resolve().parents[1]
SHA = re.compile(r'[a-f0-9]{64}')
MAX_MANIFEST_BYTES = 16 * 1024 * 1024


def revision(value):
    if (not re.fullmatch(r'[a-f0-9]{40}', value)
            or subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT,
                                       text=True).strip() != value):
        raise ValueError('REVIEWED_CHECKOUT_SHA_REQUIRED')


def transient(error):
    if isinstance(error, (OSError, TimeoutError)):
        return True
    # SDK errors never leak credential-bearing exception strings into CI logs.
    try:
        from botocore.exceptions import (ConnectTimeoutError, ConnectionClosedError, EndpointConnectionError,
                                        ReadTimeoutError, IncompleteReadError,
                                        ResponseStreamingError, ClientError)
        if isinstance(error, ClientError):
            status = error_details(error)[1]
            return status == 429 or (status is not None and 500 <= status <= 599)
        return isinstance(error, (ConnectTimeoutError, ConnectionClosedError, EndpointConnectionError,
                                  ReadTimeoutError, IncompleteReadError, ResponseStreamingError))
    except ImportError:
        return False


def read_verified(client, bucket, key, sha256, size, capture=False):
    if capture and size > MAX_MANIFEST_BYTES:
        raise ValueError('MANIFEST_TOO_LARGE')
    for attempt in range(3):
        body = None
        try:
            response = client.get_object(Bucket=bucket, Key=key)
            body = response['Body']
            if response.get('ContentLength', size) != size:
                raise ValueError('REMOTE_LENGTH_MISMATCH')
            digest, received, chunks = hashlib.sha256(), 0, []
            for chunk in iter(lambda: body.read(1024 * 1024), b''):
                received += len(chunk)
                if received > size:
                    raise ValueError('REMOTE_LENGTH_MISMATCH')
                digest.update(chunk)
                if capture:
                    chunks.append(chunk)
            if received != size or digest.hexdigest() != sha256:
                raise ValueError('REMOTE_CONTENT_MISMATCH')
            return b''.join(chunks) if capture else None
        except Exception as error:
            if not transient(error) or attempt == 2:
                raise
            time.sleep(2)
        finally:
            if body is not None:
                body.close()


def validate_stage(stage, bucket, total_bytes):
    if (stage.get('schema') != 'anipals-private-archive-stage-v1'
            or stage.get('complete') is not False or stage.get('uploadsComplete') is not True
            or stage.get('bucket') != bucket or not SHA.fullmatch(str(stage.get('inputSha256', '')))):
        raise ValueError('INVALID_UNVERIFIED_STAGE')
    entries, paths = stage.get('entries'), set()
    if not isinstance(entries, list) or not 1 <= len(entries) <= 50000:
        raise ValueError('INVALID_STAGE_ENTRIES')
    for entry in entries:
        path, sha, size = entry.get('path'), entry.get('sha256'), entry.get('bytes')
        if (not isinstance(path, str) or not path or '\\' in path
                or PurePosixPath(path).is_absolute() or '..' in PurePosixPath(path).parts
                or str(PurePosixPath(path)) != path or path in paths):
            raise ValueError('INVALID_OR_DUPLICATE_SOURCE_PATH')
        paths.add(path)
        if (not isinstance(sha, str) or not SHA.fullmatch(sha)
                or type(size) is not int or size < 0
                or entry.get('key') != f'archive/sha256/{sha[:2]}/{sha}'
                or entry.get('classification') != 'private'
                or not isinstance(entry.get('purpose'), str) or not entry['purpose']
                or entry.get('uploaded') is not True or entry.get('verified') is not False
                or 'objectRef' in entry):
            raise ValueError('INVALID_STAGE_ENTRY_IDENTITY')
    if (type(stage.get('plannedFiles')) is not int or stage['plannedFiles'] != len(entries)
            or type(stage.get('plannedBytes')) is not int
            or stage['plannedBytes'] != sum(e['bytes'] for e in entries)
            or stage['plannedBytes'] != total_bytes):
        raise ValueError('STAGE_BUDGET_OR_COUNT_MISMATCH')
    return entries


def publish_receipt(client, bucket, payload):
    digest = hashlib.sha256(payload).hexdigest()
    key = f'archive/receipts/sha256/{digest}.json'
    for attempt in range(3):
        try:
            client.put_object(Bucket=bucket, Key=key, Body=payload, ContentLength=len(payload),
                              IfNoneMatch='*', ContentType='application/json',
                              CacheControl='private, no-store', Metadata={'sha256': digest})
            break
        except Exception as error:
            code, status = error_details(error)
            if code in ('PreconditionFailed', 'ConditionalRequestConflict', '412', '409') or status in (409, 412):
                break
            if not transient(error) or attempt == 2:
                raise
            time.sleep(2)
    read_verified(client, bucket, key, digest, len(payload))
    return {'key': key, 'sha256': digest, 'bytes': len(payload)}


def verify_stage(client, bucket, *, manifest_key, manifest_sha, manifest_bytes,
                 total_bytes, source_sha, progress=None):
    if (not SHA.fullmatch(manifest_sha)
            or manifest_key != f'archive/staging/sha256/{manifest_sha}.json'
            or not 0 < manifest_bytes <= MAX_MANIFEST_BYTES
            or total_bytes < 0 or not re.fullmatch(r'[a-f0-9]{40}', source_sha)):
        raise ValueError('EXACT_STAGE_IDENTITY_REQUIRED')
    raw = read_verified(client, bucket, manifest_key, manifest_sha, manifest_bytes, capture=True)
    stage = json.loads(raw)
    entries = validate_stage(stage, bucket, total_bytes)
    verified, seen = [], set()
    for entry in entries:
        identity = (entry['key'], entry['sha256'], entry['bytes'])
        if identity not in seen:
            read_verified(client, bucket, entry['key'], entry['sha256'], entry['bytes'])
            seen.add(identity)
        verified.append({k: v for k, v in entry.items() if k != 'uploaded'} | {
            'verified': True, 'objectRef': f'r2://{bucket}/{entry["key"]}'})
        if progress:
            progress({'verifiedFiles': len(verified), 'plannedFiles': len(entries)})
    receipt = {'schema': 'anipals-private-archive-v1', 'complete': True, 'entries': verified,
               'verification': {'sourceSha': source_sha, 'stageSha256': manifest_sha,
                                'stageBytes': manifest_bytes, 'sourceBytes': total_bytes}}
    payload = (json.dumps(receipt, sort_keys=True, separators=(',', ':'), ensure_ascii=False) + '\n').encode()
    return publish_receipt(client, bucket, payload)


def archival_config():
    from botocore.config import Config
    # The outer loop retries whole streams and writes, so the SDK must not multiply attempts.
    return Config(signature_version='s3v4', connect_timeout=15, read_timeout=90,
                  retries={'total_max_attempts': 1, 'mode': 'standard'})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('source-sha', 'manifest-key', 'manifest-sha'):
        parser.add_argument('--' + key, required=True)
    for key in ('manifest-bytes', 'total-bytes'):
        parser.add_argument('--' + key, type=int, required=True)
    args = parser.parse_args()
    revision(args.source_sha)
    # Imported only after source SHA validation; credentials exist only in this step.
    import boto3
    sys.path.insert(0, str(ROOT / 'deploy'))
    from regional_r2 import connection
    base, bucket = connection()
    # Existing connection validates endpoint/bucket; use archival rather than runtime timeouts.
    endpoint = base.meta.endpoint_url
    base.close()
    client = boto3.client('s3', endpoint_url=endpoint, region_name='auto',
                         aws_access_key_id=os.environ['R2_ACCESS_KEY_ID'],
                         aws_secret_access_key=os.environ['R2_SECRET_ACCESS_KEY'],
                         config=archival_config())
    try:
        result = verify_stage(client, bucket, **vars(args), progress=lambda p: print(json.dumps(p), flush=True))
    finally:
        client.close()
    # Only opaque content identities/counts appear in public logs. Never source paths or receipt content.
    print(json.dumps({'complete': True, 'privateReceipt': result}))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print(json.dumps({'complete': False, 'error': 'PRIVATE_ARCHIVE_VERIFICATION_FAILED'}), file=sys.stderr)
        raise SystemExit(1)
