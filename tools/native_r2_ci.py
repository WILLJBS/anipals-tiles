#!/usr/bin/env python3
"""Run an immutable private R2/native request; publish only opaque result descriptors."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid
from cloud_collector_io import PrivateStore
from native_r2_contract import check, descriptor, execution, native_args, validate_request
from private_archive_verify import archival_config, revision

ROOT = Path(__file__).resolve().parents[1]


def run(command, log, timeout):
    with log.open('ab') as stream:
        result = subprocess.run(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT, timeout=timeout)
    check(result.returncode == 0, 'PRIVATE_NATIVE_SUBPROCESS_FAILED')


def docker_command(request, work, name):
    command = ['docker', 'run', '--name', name, '--entrypoint', 'python3']
    for name in ('R2_ENDPOINT_URL', 'R2_BUCKET', 'R2_ACCESS_KEY_ID', 'R2_SECRET_ACCESS_KEY'):
        command += ['-e', name]  # Values never enter process argv or logs.
    command += ['-e', 'PYTHONDONTWRITEBYTECODE=1', '-e', 'PYTHONPATH=/opt/diagnostic-sdk',
                '-e', 'GIT_CONFIG_COUNT=1', '-e', 'GIT_CONFIG_KEY_0=safe.directory',
                '-e', 'GIT_CONFIG_VALUE_0=/work']
    for source, target, readonly in [(ROOT, '/work', True), (ROOT/'tests', '/diagnostic', True),
                                     (work/'input', '/input', True), (work/'output', '/output', False)]:
        command += ['--mount', 'type=bind,src=%s,dst=%s%s' % (source, target, ',readonly' if readonly else '')]
    return command + ['anipals-native-r2:'+request['diagnosticSha']] + native_args(request)


def private_log(store, prefix, path):
    size = path.stat().st_size if path.exists() else 0
    with path.open('rb') as stream:
        stream.seek(max(0, size-512*1024))
        tail = stream.read(512*1024).decode('utf8', errors='replace')
    return store.save_json(prefix+'logs/', dict(schema=1, totalBytes=size, truncated=size>512*1024, text=tail))


def execute(store, request, request_ref, context, work, runner=run):
    request = validate_request(request, context['runnerSourceSha'])
    work.mkdir(parents=True, exist_ok=True)
    (work/'input').mkdir(); (work/'output').mkdir()
    log = work/'private.log'; log.touch()
    phase, native, error = 'private-input', None, None
    name = 'anipals-native-r2-'+uuid.uuid4().hex
    try:
        for key, filename in [('release', 'release.json'), ('ready', 'READY'), ('probe', 'probe.json')]:
            store.fetch(request[key], work/'input'/filename)
        phase = 'candidate-build'
        image = (ROOT/'deploy/valhalla-image.txt').read_text().strip()
        base = 'anipals-native-base:'+request['diagnosticSha']
        runner(['docker', 'build', '-f', 'deploy/Dockerfile', '--build-arg', 'VALHALLA_IMAGE='+image,
                '--build-arg', 'REVISION='+request['diagnosticSha'], '-t', base, '.'], log, 600)
        phase = 'diagnostic-build'
        runner(['docker', 'build', '-f', 'tests/Dockerfile.native-r2', '--build-arg', 'CANDIDATE_IMAGE='+base,
                '-t', 'anipals-native-r2:'+request['diagnosticSha'], '.'], log, 300)
        phase = 'native-cold-hot'
        try:
            runner(docker_command(request, work, name), log, 180)
        finally:
            # Killing docker CLI on a timeout does not stop its container.
            # Always stop this uniquely owned diagnostic, including timeouts.
            runner(['docker', 'rm', '-f', name], log, 30)
        path = work/'output/result.json'
        check(path.stat().st_size <= 65536, 'PRIVATE_NATIVE_RESULT_TOO_LARGE')
        native = json.loads(path.read_text())
        check(native.get('event') == 'REAL_R2_NATIVE_COLD_HOT_PASSED'
              and native.get('diagnostic_sha') == request['diagnosticSha']
              and native.get('migration_sha') == request['migrationSha']
              and native.get('receipt_sha256') == request['receipt']['sha256']
              and native.get('manifest_sha256') == request['manifest']['sha256']
              and native.get('probe_sha256') == request['probe']['sha256']
              and native.get('graph_fingerprint') == request['graphFingerprint']
              and native.get('memory_mb') == 768 and native.get('timeout_seconds') == 8
              and native.get('production_activated') is False, 'PRIVATE_NATIVE_RESULT_IDENTITY_DIFFERS')
        phase = 'complete'
    except Exception as failure:
        error = type(failure).__name__  # No SDK messages, URLs, argv or coordinates.
    prefix = 'navigation/native-acceptance/'+request_ref['sha256']+'/'
    log_ref = private_log(store, prefix, log)
    result = dict(schema='anipals-native-r2-result-v1', complete=error is None, productionActivated=False,
                  request=request_ref, execution=context, phase=phase, error=error,
                  native=native if error is None else None, privateLog=log_ref)
    ref = store.save_json(prefix+'results/', result)
    public = dict(complete=error is None, privateResult=ref, productionActivated=False)
    if error is None:
        public['counters'] = [{key: record[key] for key in ('cache', 'http_gets', 'object_fetches', 'source_bytes', 'elapsed_ms')}
                              for record in native['records']]
    return public


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('source-sha', 'input-key', 'input-sha'):
        p.add_argument('--'+name, required=True)
    p.add_argument('--input-bytes', type=int, required=True)
    args = p.parse_args()
    revision(args.source_sha)
    context = execution(args.source_sha)
    check(not subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT), 'CLEAN_REVIEWED_CHECKOUT_REQUIRED')
    ref = descriptor(dict(key=args.input_key, sha256=args.input_sha, bytes=args.input_bytes), 65536)
    sys.path.insert(0, str(ROOT/'deploy'))
    from regional_r2 import connection
    import boto3
    base, bucket = connection(); endpoint = base.meta.endpoint_url; base.close()
    client = boto3.client('s3', endpoint_url=endpoint, region_name='auto',
                         aws_access_key_id=os.environ['R2_ACCESS_KEY_ID'],
                         aws_secret_access_key=os.environ['R2_SECRET_ACCESS_KEY'], config=archival_config())
    store = PrivateStore(client, bucket)
    try:
        request = validate_request(store.json(ref['key'], ref['sha256'], ref['bytes']), args.source_sha)
        with tempfile.TemporaryDirectory(prefix='anipals-private-native-') as temp:
            result = execute(store, request, ref, context, Path(temp))
    finally:
        client.close()
    print(json.dumps(result), flush=True)
    if not result['complete']:
        raise SystemExit(2)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print(json.dumps(dict(complete=False, error='PRIVATE_NATIVE_ACCEPTANCE_FAILED')), file=sys.stderr)
        raise SystemExit(1) from None
