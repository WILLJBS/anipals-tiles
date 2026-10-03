#!/usr/bin/env python3
"""Pin CI migration inputs and keep verified receipts in private R2 objects."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'deploy'))
from migration_transport import connection
from migration_r2 import Publisher
from migration_contract import load_profile, source_contract, source_revision, validate_profile_supply

API = 'https://api.github.com/repos/WILLJBS/anipals-tiles'
TAG = re.compile(r'tiles-[a-zA-Z0-9.-]{1,100}')
SHA = re.compile(r'[0-9a-f]{64}')


class NoMetadataRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, newurl):
        raise ValueError('authenticated metadata redirect refused')


def fetch(url, token=None, limit=16*1024*1024):
    headers = {'User-Agent': 'anipals-verified-migration'}
    if token:
        if not url.startswith(API + '/'):
            raise ValueError('GitHub token cannot be sent outside metadata API')
        headers['Authorization'] = 'Bearer ' + token
        headers['Accept'] = 'application/vnd.github+json'
    opener = urllib.request.build_opener(NoMetadataRedirect()) if token else None
    for attempt in range(3):
        try:
            request = urllib.request.Request(url, headers=headers)
            with (opener.open(request, timeout=30) if opener else urllib.request.urlopen(request, timeout=30)) as response:
                raw = response.read(limit + 1)
            if len(raw) > limit:
                raise ValueError('metadata exceeds bounded size')
            return raw
        except (OSError, TimeoutError):
            if attempt == 2:
                raise OSError('GitHub migration metadata retrieval failed') from None
            time.sleep(2)


def prepare(tag, work, contract_name='original61', source_sha=None):
    if not TAG.fullmatch(tag):
        raise ValueError('explicit release tag required')
    token = os.environ.get('GH_TOKEN')
    release = json.loads(fetch(API + '/releases/tags/' + tag, token))
    if release.get('tag_name') != tag or type(release.get('id')) is not int:
        raise ValueError('release metadata identity differs')
    assets = []
    for page in range(1, 101):
        batch = json.loads(fetch(API + '/releases/%d/assets?per_page=100&page=%d' % (release['id'], page), token))
        if not isinstance(batch, list):
            raise ValueError('invalid release assets response')
        assets.extend(batch)
        if len(batch) < 100:
            break
    else:
        raise ValueError('release assets exceed bounded pagination')
    release['assets'] = assets
    marker = next(a for a in assets if a['name'] == 'READY')
    expected = 'https://github.com/WILLJBS/anipals-tiles/releases/download/' + tag + '/READY'
    if marker['browser_download_url'] != expected:
        raise ValueError('READY URL differs from exact source release')
    ready = fetch(expected, limit=4*1024*1024)
    profile = load_profile(contract_name)
    plans = validate_profile_supply(profile, release, ready)
    bound = source_contract(profile, source_sha) if source_sha is not None else None
    work.mkdir(parents=True, exist_ok=True)
    (work/'release.json').write_text(json.dumps(release))
    (work/'READY').write_bytes(ready)
    if bound is not None:
        (work/'source-contract.json').write_text(json.dumps(bound, sort_keys=True))
    return [plan['slug'] for plan in plans]


def receipt_key(source_sha, tag, name, digest):
    if (not re.fullmatch('[0-9a-f]{40}', source_sha) or not TAG.fullmatch(tag)
            or not re.fullmatch(r'(?:pilot|receipt-[a-z0-9-]+)', name) or not SHA.fullmatch(digest)):
        raise ValueError('invalid private migration receipt identity')
    return 'navigation/migration-receipts/%s/%s/%s/%s.json' % (source_sha, tag, name, digest)


def publish_receipt(source_sha, tag, path, name):
    raw = path.read_bytes()
    if not 0 < len(raw) <= 8*1024*1024:
        raise ValueError('receipt outside bounded size')
    receipt = json.loads(raw)
    s3, bucket = connection()
    if receipt.get('schema') != 1 or receipt.get('bucket') != bucket or not SHA.fullmatch(receipt.get('contract', '')):
        raise ValueError('receipt does not bind current R2 bucket and source contract')
    if name == 'pilot' and (receipt.get('verified_tiles') != 20 or receipt.get('manifest_published') is not False):
        raise ValueError('successful twenty-tile pilot receipt required')
    if name != 'pilot' and (name != 'receipt-' + receipt.get('slug', '')
            or not SHA.fullmatch(receipt.get('manifest_sha256', ''))
            or not SHA.fullmatch(receipt.get('graph_fingerprint', ''))
            or type(receipt.get('manifest_size')) is not int or receipt['manifest_size'] <= 0
            or type(receipt.get('tiles')) is not int or receipt['tiles'] <= 0):
        raise ValueError('incomplete or substituted regional receipt')
    digest = hashlib.sha256(raw).hexdigest()
    key = receipt_key(source_sha, tag, name, digest)
    Publisher(s3, bucket).put(key, path, dict(size=len(raw), sha256=digest))
    return dict(key=key, sha256=digest, size=len(raw))


def read_pilot(source_sha, tag, digest, size, output):
    from botocore.exceptions import BotoCoreError, ClientError
    if type(size) is not int or not 0 < size <= 65536:
        raise ValueError('invalid pilot receipt size')
    key = receipt_key(source_sha, tag, 'pilot', digest)
    s3, bucket = connection()
    try:
        response = s3.get_object(Bucket=bucket, Key=key)
        body = response['Body']
        try:
            raw = body.read(size + 1)
        finally:
            body.close()
    except (BotoCoreError, ClientError, OSError):
        raise OSError('private pilot receipt retrieval failed') from None
    if len(raw) != size or hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError('private pilot receipt failed complete GET SHA/size')
    receipt = json.loads(raw)
    if (receipt.get('schema') != 1 or not SHA.fullmatch(receipt.get('contract', ''))
            or receipt.get('bucket') != bucket or receipt.get('verified_tiles') != 20
            or receipt.get('manifest_published') is not False):
        raise ValueError('private receipt is not the matching successful pilot')
    output.write_bytes(raw)


def outputs(values):
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a') as file:
            for key, value in values.items():
                file.write(key + '=' + (json.dumps(value) if not isinstance(value, str) else value) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-sha', required=True)
    parser.add_argument('--contract', default='original61')
    parser.add_argument('--tag', required=True)
    parser.add_argument('--work', type=Path, required=True)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('prepare')
    publish = sub.add_parser('publish-receipt')
    publish.add_argument('--name', required=True); publish.add_argument('--file', type=Path, required=True)
    fetcher = sub.add_parser('fetch-pilot')
    fetcher.add_argument('--sha256', required=True); fetcher.add_argument('--size', type=int, required=True)
    args = parser.parse_args()
    source_revision(args.source_sha)
    if args.command == 'prepare':
        regions = prepare(args.tag, args.work, args.contract, args.source_sha)
        outputs(dict(regions=regions)); print(json.dumps(dict(validated_regions=len(regions))))
    elif args.command == 'publish-receipt':
        result = publish_receipt(args.source_sha, args.tag, args.file, args.name)
        outputs(result); print(json.dumps(dict(event='private_receipt_GET_verified', sha256=result['sha256'])))
    else:
        read_pilot(args.source_sha, args.tag, args.sha256, args.size, args.work/'pilot-receipt.json')
        print('Private pilot receipt GET/SHA verified.')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps(dict(event='migration_ci_failed', code=type(error).__name__)), file=sys.stderr)
        raise SystemExit(1) from None
