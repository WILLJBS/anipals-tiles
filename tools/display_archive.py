#!/usr/bin/env python3
"""Fixed official PMTiles → private R2; bounded Range parts and full SHA gates."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import signal
import re
import subprocess
import sys
import time
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'deploy'))
from regional_storage import atomic_json as save
from regional_r2 import connection
from storage_errors import error_details


def conflict(error):
    code, status = error_details(error)
    return code in ('PreconditionFailed', 'ConditionalRequestConflict', '412', '409') or status in (409, 412)


def cloud_connection():
    # Reuse the runtime's endpoint/bucket validation, but not its short read budget.
    from boto3 import client
    from botocore.config import Config
    validated, bucket = connection()
    endpoint = validated.meta.endpoint_url
    validated.close()
    return client('s3', endpoint_url=endpoint, region_name='auto',
                  aws_access_key_id=os.environ['R2_ACCESS_KEY_ID'],
                  aws_secret_access_key=os.environ['R2_SECRET_ACCESS_KEY'],
                  config=Config(signature_version='s3v4', connect_timeout=15, read_timeout=120,
                                retries={'total_max_attempts': 3, 'mode': 'standard'})), bucket

SOURCE = 'https://btrfs.openfreemap.com/areas/planet/20260927_080001_pt/tiles.pmtiles'
SIZE = 86753200519
SHA256 = 'bae742bf7a931eb598e3a8c8dd9f287c3bc6bfc4b380ed0f3c614aba06b8ecd6'
KEY = 'basemaps/openfreemap-20260927/tiles.pmtiles'
PART_SIZE = 64 * 1024 * 1024


def read_range(start, end, size, opener=urlopen, sleep=time.sleep):
    expected = end - start + 1
    for attempt in range(3):
        try:
            request = Request(SOURCE, headers={'Range': f'bytes={start}-{end}', 'Accept-Encoding': 'identity'})
            with opener(request, timeout=180) as response:
                if (response.status != 206 or response.headers.get('Content-Range') != f'bytes {start}-{end}/{size}'
                        or response.headers.get('Content-Encoding', 'identity') != 'identity'
                        or response.geturl() != SOURCE):
                    raise ValueError('SOURCE_RANGE_IDENTITY_MISMATCH')
                data = response.read(expected + 1)
                if len(data) != expected:
                    raise ValueError('SOURCE_RANGE_SIZE_MISMATCH')
                return data
        except Exception:
            if attempt == 2:
                raise
            sleep(2)


def verify_remote(client, bucket, key, size, sha256):
    response = client.get_object(Bucket=bucket, Key=key)
    digest, count = hashlib.sha256(), 0
    try:
        for data in iter(lambda: response['Body'].read(8 * 1024 * 1024), b''):
            count += len(data)
            if count > size:
                raise ValueError('REMOTE_SIZE_MISMATCH')
            digest.update(data)
    finally:
        response['Body'].close()
    if count != size or digest.hexdigest() != sha256:
        raise ValueError('REMOTE_SHA_MISMATCH')
    if response.get('Metadata', {}).get('sha256') != sha256:
        raise ValueError('REMOTE_METADATA_MISMATCH')
    etag = response.get('ETag', '').strip('"')
    if not etag:
        raise ValueError('REMOTE_ETAG_MISSING')
    return {'key': key, 'bytes': size, 'sha256': sha256, 'etag': etag}


def transfer(client, bucket, *, key=KEY, size=SIZE, sha256=SHA256, part_size=PART_SIZE,
             reader=read_range, progress=lambda value: None):
    try:
        existing = client.head_object(Bucket=bucket, Key=key)
    except Exception as error:
        if error_details(error)[0] not in ('404', 'NoSuchKey', 'NotFound'):
            raise
        existing = None
    if existing is not None:
        return verify_remote(client, bucket, key, size, sha256)
    request = {'Bucket': bucket, 'Key': key}
    request['UploadId'] = client.create_multipart_upload(**request, Metadata={'sha256': sha256},
        ContentType='application/octet-stream', CacheControl='public, max-age=31536000, immutable')['UploadId']
    complete, failure = False, None
    try:
        digest, parts, total = hashlib.sha256(), [], 0
        for start in range(0, size, part_size):
            end = min(size, start + part_size) - 1
            data = reader(start, end, size)
            if len(data) != end - start + 1:
                raise ValueError('SOURCE_PART_SIZE_MISMATCH')
            digest.update(data);total += len(data)
            result = client.upload_part(**request, PartNumber=len(parts) + 1, Body=data,
                ContentMD5=base64.b64encode(hashlib.md5(data).digest()).decode('ascii'))
            parts.append({'PartNumber': len(parts) + 1, 'ETag': result['ETag']})
            progress({'uploadedBytes': total, 'totalBytes': size, 'parts': len(parts), 'complete': False})
        if total != size or digest.hexdigest() != sha256:
            raise ValueError('FULL_SOURCE_SHA_MISMATCH')
        try:
            client.complete_multipart_upload(**request, MultipartUpload={'Parts': parts}, IfNoneMatch='*')
            complete = True
        except Exception as error:
            if not conflict(error):
                raise
        # An existing concurrent winner is only acceptable after full GET/SHA.
        return verify_remote(client, bucket, key, size, sha256)
    except BaseException as error:
        failure = error
        raise
    finally:
        if not complete:
            try:
                client.abort_multipart_upload(**request)
            except Exception:
                if failure is None:
                    raise
                failure.multipart_abort_failed = True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--source-sha')
    args = parser.parse_args()
    if not args.apply:
        print(json.dumps({'source': SOURCE, 'bytes': SIZE, 'sha256': SHA256, 'key': KEY, 'partBytes': PART_SIZE}))
        return
    def interrupted(*unused):
        raise KeyboardInterrupt('INTERRUPTED')
    signal.signal(signal.SIGTERM, interrupted)
    if not re.fullmatch(r'[a-f0-9]{40}', args.source_sha or '') or subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip() != args.source_sha:
        raise ValueError('EXACT_REVIEWED_SOURCE_SHA_REQUIRED')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    client, bucket = cloud_connection()
    def progress(value):
        save(args.output, {'schema': 'anipals-basemap-archive-receipt-v1', **value})
        print(json.dumps(value), flush=True)
    result = transfer(client, bucket, progress=progress)
    save(args.output, {'schema': 'anipals-basemap-archive-receipt-v1', 'complete': True,
                       'source': SOURCE, 'archive': result})
    print(json.dumps({'complete': True, 'bytes': result['bytes']}))


if __name__ == '__main__':
    try:
        main()
    except BaseException as error:
        if isinstance(error, SystemExit):
            raise
        code = error_details(error)[0]
        print(json.dumps({'error': code or type(error).__name__,
                          'reason': str(error) if isinstance(error, ValueError) else 'BASEMAP_TRANSFER_FAILED',
                          'multipart_abort_failed': bool(getattr(error, 'multipart_abort_failed', False))}), file=sys.stderr)
        raise SystemExit(1)
