#!/usr/bin/env python3
"""Private bounded cloud pilot using an exact unchanged collector code bundle."""
import argparse
from contextlib import redirect_stdout, redirect_stderr
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import signal
import sys
import time
import zipfile
from cloud_collector_io import PrivateStore, Journal
from cloud_collector_net import cloud_range_class, wrap_collect
from cloud_execution import github_execution
from private_archive_verify import revision, archival_config


class Deadline(BaseException): pass


def unpack(path, root, expected_sha):
    with zipfile.ZipFile(path) as bundle:
        names = bundle.namelist()
        if len(names) != len(set(names)) or 'collector-code.json' not in names:
            raise ValueError('CODE_BUNDLE_DUPLICATE_OR_MANIFEST_MISSING')
        if sum(i.file_size for i in bundle.infolist()) > 4_000_000:
            raise ValueError('CODE_BUNDLE_TOO_LARGE')
        manifest = json.loads(bundle.read('collector-code.json'))
        if manifest.get('schema') != 'anipals-collector-code-v1' or manifest.get('appSourceSha') != expected_sha:
            raise ValueError('CODE_SOURCE_SHA_MISMATCH')
        if set(names) != set(manifest['files']) | {'collector-code.json'}:
            raise ValueError('CODE_BUNDLE_FILE_SET_DIFFERS')
        for name, sha in manifest['files'].items():
            allowed = (re.fullmatch(r'tools/(discovery-places|discovery-release)/[a-z_]+\.py', name)
                       or name in ('data/discovery/place-types.json', 'tools/object-storage/immutable_upload.py', 'tools/object-storage/storage_errors.py'))
            if not allowed or PurePosixPath(name).is_absolute() or '..' in PurePosixPath(name).parts:
                raise ValueError('CODE_BUNDLE_PATH_REJECTED')
            raw = bundle.read(name)
            if hashlib.sha256(raw).hexdigest() != sha: raise ValueError('CODE_FILE_SHA_MISMATCH')
            target = root/name; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(raw)


def validate(spec):
    if (spec.get('schema') != 'anipals-cloud-collector-v1' or spec.get('mode') != 'pilot'
            or spec.get('globalScopeCount') != 6222 or spec.get('workers') != 1
            or spec.get('themes') != ['base', 'places'] or spec.get('publicationsAllowed') is not False
            or spec.get('seeds') != [] or spec.get('priorSourceBytes') != 0):
        raise ValueError('UNREVIEWED_COLLECTOR_CONFIGURATION')
    if (not 1 <= len(spec['selectedNames']) <= 20 or len(set(spec['selectedNames'])) != len(spec['selectedNames'])
            or len(spec['selectedGeoNamesIds']) != len(spec['selectedNames'])
            or type(spec['sourceBudgetBytes']) is not int or not 1 <= spec['sourceBudgetBytes'] <= 78_760_503_773
            or type(spec['maxMinutes']) is not int or not 1 <= spec['maxMinutes'] <= 25
            or not re.fullmatch('[a-f0-9]{40}', spec['appSourceSha'])):
        raise ValueError('PILOT_SCOPE_OR_BUDGET_INVALID')
    for field in ('roster', 'index', 'code'):
        d = spec[field]
        if (not re.fullmatch('[a-f0-9]{64}', d['sha256']) or type(d['bytes']) is not int or not 1 <= d['bytes'] <= {'roster': 16_000_000, 'index': 4_000_000, 'code': 4_000_000}[field]
                or d['key'] != f'archive/sha256/{d["sha256"][:2]}/{d["sha256"]}'):
            raise ValueError('SOURCE_BLOB_IDENTITY_REQUIRED')


def bundled_uploader(code):
    # Keep app/tiles modules with the same basename isolated. Source bytes remain unchanged.
    folder = code/'tools/object-storage'
    def load(name, path):
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module
    previous = sys.modules.get('storage_errors')
    try:
        sys.modules['storage_errors'] = load('_collector_archive_errors', folder/'storage_errors.py')
        return load('_collector_immutable_upload', folder/'immutable_upload.py').upload
    finally:
        if previous is None: sys.modules.pop('storage_errors', None)
        else: sys.modules['storage_errors'] = previous


def collect(store, spec, spec_sha, work, execution=None):
    validate(spec); work.mkdir(parents=True, exist_ok=True)
    bundle = work/'code.zip'; store.fetch(spec['code'], bundle)
    code = work/'code'; unpack(bundle, code, spec['appSourceSha'])
    sys.path.insert(0, str(code/'tools/discovery-places'))
    store.uploader = bundled_uploader(code)
    roster = work/'roster.json'; index = work/'index.parquet'
    store.fetch(spec['roster'], roster); store.fetch(spec['index'], index)
    from city_scope import city_source_rows, city_regions
    rows = city_source_rows(json.loads(roster.read_text())); city_regions(rows)
    if len(rows) != 6222: raise ValueError('GLOBAL_ROSTER_COUNT_DIFFERS')
    by_name = {r['name']: r for r in rows}
    selected = [by_name[name] for name in spec['selectedNames']]
    if [c['sourceIdentity']['id'] for c in selected] != spec['selectedGeoNamesIds']:
        raise ValueError('SELECTED_SOURCE_IDENTITY_DIFFERS')
    selected_path = work/'selected.json'; selected_path.write_text(json.dumps({'cities': selected}))
    journal = Journal(store, spec_sha, spec['sourceBudgetBytes'])
    output = work/'output'; output.mkdir(exist_ok=True)
    if 'expand_cities' in sys.modules:
        raise ValueError('COLLECTOR_REQUIRES_FRESH_PROCESS')
    import expand_cities as engine
    engine.RangeCache = cloud_range_class(engine.RangeCache, journal, time.monotonic()+spec['maxMinutes']*60)
    engine.collect_file = wrap_collect(engine.collect_file, journal)
    args = argparse.Namespace(output=output, cities=selected_path, index=index, limit=None, proxy=None,
                              budget=spec['sourceBudgetBytes'], reuse_cache=[], workers=1, themes=['base', 'places'])
    # Original logs can contain source URLs and failure context. Retain privately.
    complete = False; summary = None; failure = None
    def expire(*unused): raise Deadline()
    old = signal.signal(signal.SIGALRM, expire); signal.alarm(spec['maxMinutes']*60)
    try:
        with (work/'collector.log').open('w') as log, redirect_stdout(log), redirect_stderr(log):
            summary = engine.run(args)
        complete = (not summary['failures'] and not summary.get('geometryLookupFailures')
                    and summary['filesCompleted'] == summary['filesTotal']
                    and set(summary.get('cityComplete', {})) == set(spec['selectedNames'])
                    and all(summary['cityComplete'].values()))
    except BaseException as error:
        failure = type(error).__name__
    finally:
        signal.alarm(0); signal.signal(signal.SIGALRM, old)
        outputs = {}
        for name in ('named-candidates.json', 'dedup-report.json', 'summary.json', 'progress.json'):
            if (output/name).is_file(): outputs[name] = store.put(output/name)
        log_ref = store.put(work/'collector.log') if (work/'collector.log').is_file() else None
        state = journal.snapshot()
        result = {'schema': 'anipals-cloud-collector-result-v1', 'complete': complete, 'specSha256': spec_sha,
                  'execution': execution if execution is not None else {'kind': 'offline'},
                  'appSourceSha': spec['appSourceSha'], 'roster': spec['roster'], 'index': spec['index'],
                  'selectedNames': spec['selectedNames'], 'selectedGeoNamesIds': spec['selectedGeoNamesIds'],
                  'sourceBytesCharged': journal.used, 'budgetBytes': journal.budget,
                  'budgetLedger': {'namespace': spec_sha, 'checkpointSequence': journal.seq,
                      'unsettledReservedBytes': sum(journal.pending.values()),
                      'scope': 'this-spec-only', 'mergeRequiredBeforeGlobalContinuation': True},
                  'candidateCount': summary.get('uniqueNamedCandidates') if summary else None,
                  'filesCompleted': summary.get('filesCompleted') if summary else None,
                  'failureType': failure, 'outputs': outputs, 'log': log_ref, 'checkpoint': state,
                  'publication': 'pending', 'navigationTargetsPublished': 0}
        receipt = store.save_json(journal.prefix+'results/', result)
    return {'complete': complete, 'pilotScopes': len(selected), 'sourceBytesCharged': journal.used,
            'unsettledReservedBytes': sum(journal.pending.values()),
            'candidateCount': result['candidateCount'], 'privateResult': receipt}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('source-sha', 'spec-key', 'spec-sha'): p.add_argument('--'+name, required=True)
    p.add_argument('--spec-bytes', required=True, type=int); a = p.parse_args(); revision(a.source_sha)
    if (not re.fullmatch('[a-f0-9]{64}', a.spec_sha)
            or a.spec_key != f'archive/sha256/{a.spec_sha[:2]}/{a.spec_sha}' or not 0 < a.spec_bytes < 1_000_000):
        raise ValueError('EXACT_PRIVATE_SPEC_REQUIRED')
    execution = github_execution(a.source_sha)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'deploy'))
    from regional_r2 import connection
    import boto3
    base, bucket = connection(); endpoint = base.meta.endpoint_url; base.close()
    client = boto3.client('s3', endpoint_url=endpoint, region_name='auto',
                         aws_access_key_id=os.environ['R2_ACCESS_KEY_ID'],
                         aws_secret_access_key=os.environ['R2_SECRET_ACCESS_KEY'], config=archival_config())
    store = PrivateStore(client, bucket)
    spec = store.json(a.spec_key, a.spec_sha, a.spec_bytes)
    try: result = collect(store, spec, a.spec_sha, Path('.local/private-collector'), execution=execution)
    finally: client.close()
    print(json.dumps(result), flush=True)
    if not result['complete']: raise SystemExit(2)


if __name__ == '__main__':
    try: main()
    except Exception:
        print(json.dumps({'complete': False, 'error': 'PRIVATE_COLLECTOR_FAILED'}), file=sys.stderr)
        raise SystemExit(1)
