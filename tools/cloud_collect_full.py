#!/usr/bin/env python3
"""Prepare or resume the exact global collector; real GitHub pilot proof is mandatory."""
import argparse
import json
import os
from pathlib import Path
import re
import sys
from cloud_collector_io import PrivateStore
from cloud_execution import REPOSITORY
from cloud_full_contract import blob_json, prepare_contract
from cloud_full_github import actual_proof
from cloud_full_runtime import load_code, collect_full
from private_archive_verify import revision, archival_config

WORKFLOW = '.github/workflows/collect-private-places-full.yml'


def execution(source_sha):
    env = os.environ; ref = env.get('GITHUB_REF', '')
    if (env.get('GITHUB_ACTIONS') != 'true' or env.get('GITHUB_REPOSITORY') != REPOSITORY
            or env.get('GITHUB_SHA') != source_sha or env.get('GITHUB_WORKFLOW_SHA') != source_sha
            or not re.fullmatch(r'refs/(heads|tags)/[^\s]+', ref)
            or env.get('GITHUB_WORKFLOW_REF') != f'{REPOSITORY}/{WORKFLOW}@{ref}'
            or not re.fullmatch('[1-9][0-9]{0,19}', env.get('GITHUB_RUN_ID', ''))
            or not re.fullmatch('[1-9][0-9]{0,8}', env.get('GITHUB_RUN_ATTEMPT', ''))):
        raise ValueError('TRUSTED_FULL_GITHUB_CONTEXT_REQUIRED')
    return {'kind': 'github-actions', 'repository': REPOSITORY, 'workflowPath': WORKFLOW,
            'runId': int(env['GITHUB_RUN_ID']), 'runAttempt': int(env['GITHUB_RUN_ATTEMPT']),
            'runnerSourceSha': source_sha, 'workflowSha': source_sha, 'ref': ref}


def save(store, work, name, value):
    path = work/name; path.write_text(json.dumps(value, sort_keys=True)); return store.put(path)


def rebuild_contract(store, request, work, exclude=None):
    refs = request.get('pilotResults')
    if not isinstance(refs, list) or not 1 <= len(refs) <= 20:
        raise ValueError('ACTUAL_CLOUD_PILOT_RESULTS_REQUIRED')
    # Bootstrapping only loads a SHA-locked reviewed bundle. No source requests run here.
    first = blob_json(store, refs[0])
    from cloud_full_contract import namespace_spec
    pilot = namespace_spec(store, first['specSha256'])
    load_code(store, pilot, work)
    proofs = [actual_proof(store, ref, work/f'proof-{i}') for i, ref in enumerate(refs)]
    return prepare_contract(store, request['seed'], request['seedArchiveReceipt'], proofs, exclude)


def prepare(store, request, work):
    if request.get('schema') != 'anipals-global-collector-request-v1': raise ValueError('FULL_REQUEST_REQUIRED')
    minutes = request.get('maxMinutes')
    if type(minutes) is not int or not 1 <= minutes <= 300: raise ValueError('FULL_DURATION_BOUND')
    contract = rebuild_contract(store, request, work)
    contract_ref = save(store, work, 'contract.json', contract)
    spec = {'schema': 'anipals-cloud-collector-v1', 'mode': 'full', 'globalScopeCount': 6222,
            'workers': 1, 'themes': ['base', 'places'], 'publicationsAllowed': False,
            'code': contract['collectorCode'], 'appSourceSha': contract['appSourceSha'],
            'roster': contract['roster'], 'index': contract['index'], 'contract': contract_ref,
            'sourceBudgetBytes': contract['sourceBudgetBytes'], 'priorSourceBytes': contract['priorSourceBytes'],
            'maxMinutes': minutes}
    return {'prepared': True, 'complete': False, 'globalScopes': 6222,
            'priorSourceBytes': spec['priorSourceBytes'], 'remainingSourceBytes': contract['remainingSourceBytes'],
            'privateSpec': save(store, work, 'spec.json', spec)}


def validate_full(spec):
    if (spec.get('schema') != 'anipals-cloud-collector-v1' or spec.get('mode') != 'full'
            or spec.get('globalScopeCount') != 6222 or spec.get('workers') != 1
            or spec.get('themes') != ['base', 'places'] or spec.get('publicationsAllowed') is not False
            or type(spec.get('maxMinutes')) is not int or not 1 <= spec['maxMinutes'] <= 300):
        raise ValueError('EXACT_GLOBAL_CONFIGURATION_REQUIRED')


def resume(store, spec, spec_sha, work, context):
    validate_full(spec)
    contract = blob_json(store, spec['contract'])
    request = {'seed': contract['seed'], 'seedArchiveReceipt': contract['seedArchiveReceipt'],
               'pilotResults': [p['result'] for p in contract['verifiedPilotProofs']]}
    fresh = rebuild_contract(store, request, work, spec_sha)
    for field in ('lineageSha256', 'priorSourceBytes', 'sourceBudgetBytes', 'collectorCode', 'appSourceSha', 'roster', 'index'):
        if fresh[field] != contract[field]: raise ValueError('FULL_CONTRACT_REVALIDATION_DIFFERS')
    for source, target in [('code', 'collectorCode'), ('appSourceSha', 'appSourceSha'), ('roster', 'roster'),
                           ('index', 'index'), ('sourceBudgetBytes', 'sourceBudgetBytes'), ('priorSourceBytes', 'priorSourceBytes')]:
        if spec[source] != contract[target]: raise ValueError('FULL_SPEC_CONTRACT_DIFFERS')
    return collect_full(store, spec, spec_sha, contract, blob_json(store, contract['seed']), work, context)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('operation', choices=('prepare', 'execute'))
    for name in ('source-sha', 'input-key', 'input-sha'): p.add_argument('--'+name, required=True)
    p.add_argument('--input-bytes', required=True, type=int); a = p.parse_args(); revision(a.source_sha)
    if (not re.fullmatch('[a-f0-9]{64}', a.input_sha)
            or a.input_key != f'archive/sha256/{a.input_sha[:2]}/{a.input_sha}' or not 0 < a.input_bytes < 1_000_000):
        raise ValueError('EXACT_PRIVATE_INPUT_REQUIRED')
    context = execution(a.source_sha)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'deploy'))
    from regional_r2 import connection
    import boto3
    base, bucket = connection(); endpoint = base.meta.endpoint_url; base.close()
    client = boto3.client('s3', endpoint_url=endpoint, region_name='auto',
                         aws_access_key_id=os.environ['R2_ACCESS_KEY_ID'],
                         aws_secret_access_key=os.environ['R2_SECRET_ACCESS_KEY'], config=archival_config())
    store = PrivateStore(client, bucket); work = Path('.local/private-full-collector'); work.mkdir(parents=True, exist_ok=True)
    try:
        value = store.json(a.input_key, a.input_sha, a.input_bytes)
        result = prepare(store, value, work) if a.operation == 'prepare' else resume(store, value, a.input_sha, work, context)
    finally: client.close()
    print(json.dumps(result), flush=True)
    if a.operation == 'execute' and not result['complete']: raise SystemExit(2)


if __name__ == '__main__':
    try: main()
    except Exception:
        print(json.dumps({'complete': False, 'error': 'PRIVATE_GLOBAL_COLLECTOR_FAILED'}), file=sys.stderr)
        raise SystemExit(1) from None
