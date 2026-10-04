"""Read-only full-run gates: immutable partial seeds, all namespace spend, external CI proof."""
import hashlib
import json
import re
from cloud_collector_io import Journal, retry
from cloud_execution import REPOSITORY, WORKFLOW
from private_archive_verify import read_verified


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def blob_json(store, descriptor):
    return store.json(descriptor['key'], descriptor['sha256'], descriptor['bytes'])


def namespace_spec(store, namespace):
    key = f'archive/sha256/{namespace[:2]}/{namespace}'
    head = retry(lambda: store.client.head_object(Bucket=store.bucket, Key=key))
    if not 0 < head['ContentLength'] < 1_000_000: raise ValueError('SPEC_LENGTH_REJECTED')
    return store.json(key, namespace, head['ContentLength'])


def discover_spend(store, seed, exclude_namespace=None):
    namespaces = set()
    for row in store.list('archive/collector/'):
        match = re.match(r'^archive/collector/([a-f0-9]{64})/', row['Key'])
        if not match: raise ValueError('UNKNOWN_COLLECTOR_NAMESPACE')
        namespaces.add(match[1])
    ledgers = []
    for namespace in sorted(namespaces - {exclude_namespace}):
        spec = namespace_spec(store, namespace)
        if spec.get('index', {}).get('sha256') != seed['index']['sha256']: continue
        if spec.get('roster', {}).get('sha256') != seed['roster']['sha256']:
            raise ValueError('AMBIGUOUS_SHARED_SOURCE_BUDGET')
        if spec.get('mode') not in ('pilot', 'full') or spec.get('publicationsAllowed') is not False:
            raise ValueError('UNACCOUNTED_COLLECTOR_MODE')
        prior = spec.get('priorSourceBytes')
        if type(prior) is not int or prior < 0 or (spec['mode'] == 'pilot' and prior != 0):
            raise ValueError('NAMESPACE_PRIOR_CHARGE_REQUIRED')
        journal = Journal(store, namespace, spec['sourceBudgetBytes'], prior)
        increment = journal.used-prior
        if increment < 0: raise ValueError('NAMESPACE_CHARGE_REGRESSION')
        events = sorted(({'key': row['Key'], 'bytes': row['Size']} for row in store.list(journal.prefix)), key=lambda r: r['key'])
        # Results/logs are not budget events: a new copy of a receipt cannot reset spending.
        events = [e for e in events if '/journal/' in e['key'] or '/snapshots/' in e['key']]
        ledgers.append({'namespace': namespace, 'mode': spec['mode'], 'sequence': journal.seq,
                        'sourceBytesCharged': journal.used, 'priorSourceBytes': prior,
                        'additionalSourceBytes': increment, 'unsettledUpperBytes': sum(journal.pending.values()),
                        'eventsSha256': digest(events)})
    return ledgers


def verify_pilot_proof(store, proof, seed):
    result_ref = proof['result']; result = blob_json(store, result_ref)
    namespace = result.get('specSha256', '')
    if result_ref['key'] != f'archive/collector/{namespace}/results/{result_ref["sha256"]}.json':
        raise ValueError('PILOT_RESULT_KEY_DIFFERS')
    spec = namespace_spec(store, namespace); execution = result.get('execution', {})
    if (result.get('schema') != 'anipals-cloud-collector-result-v1' or result.get('complete') is not True
            or execution.get('kind') != 'github-actions' or spec.get('mode') != 'pilot'
            or not 1 <= len(spec['selectedNames']) <= 20
            or result.get('publication') != 'pending' or result.get('navigationTargetsPublished') != 0
            or result.get('roster', {}).get('sha256') != seed['roster']['sha256']
            or result.get('index', {}).get('sha256') != seed['index']['sha256']
            or result.get('selectedNames') != spec['selectedNames']
            or result.get('selectedGeoNamesIds') != spec['selectedGeoNamesIds']
            or result.get('appSourceSha') != spec['appSourceSha']):
        raise ValueError('REAL_COMPLETE_PILOT_REQUIRED')
    run = blob_json(store, proof['githubRun'])
    if (run.get('id') != execution.get('runId') or run.get('run_attempt') != execution.get('runAttempt')
            or run.get('head_sha') != execution.get('runnerSourceSha')
            or execution.get('workflowSha') != execution.get('runnerSourceSha')
            or not re.fullmatch('[a-f0-9]{40}', str(run.get('head_sha')))
            or run.get('status') != 'completed' or run.get('conclusion') != 'success'
            or run.get('event') != 'workflow_dispatch' or run.get('path') != WORKFLOW
            or run.get('repository', {}).get('full_name') != REPOSITORY
            or execution.get('repository') != REPOSITORY or execution.get('workflowPath') != WORKFLOW):
        raise ValueError('INDEPENDENT_GITHUB_SUCCESS_PROOF_REQUIRED')
    log_ref = proof['githubLog']
    raw = read_verified(store.client, store.bucket, log_ref['key'], log_ref['sha256'], log_ref['bytes'], capture=True)
    matching = False
    for line in raw.decode('utf8').splitlines():
        start = line.find('{')
        if start < 0: continue
        try: value = json.loads(line[start:])
        except json.JSONDecodeError: continue
        if value.get('complete') is True and value.get('privateResult') == result_ref: matching = True
    if not matching: raise ValueError('GITHUB_LOG_RESULT_IDENTITY_REQUIRED')
    return {'namespace': namespace, 'collectorCode': spec['code'], 'appSourceSha': spec['appSourceSha'], 'result': result_ref, 'githubRun': proof['githubRun'], 'githubLog': log_ref,
            'runId': execution['runId'], 'runAttempt': execution['runAttempt'], 'runnerSourceSha': execution['runnerSourceSha']}


def verify_seed_receipt(seed, seed_ref, receipt):
    descriptors = [seed_ref, seed['transferLedger'], seed['diagnosticLedger'], seed['rangeBudget'], seed['progress'], seed['summary']]
    descriptors += [f['blob'] for f in seed['completedSourceFiles']]
    descriptors += [d for r in seed['ranges'] for d in (r['blob'], r['metadataBlob'])]
    if receipt.get('schema') != 'anipals-private-archive-v1' or receipt.get('complete') is not True:
        raise ValueError('VERIFIED_SEED_ARCHIVE_REQUIRED')
    for d in descriptors:
        matches = [e for e in receipt['entries'] if all(e.get(k) == d[k] for k in ('key', 'sha256', 'bytes'))]
        if not matches or any(e.get('verified') is not True for e in matches):
            raise ValueError('SEED_ARCHIVE_SOURCE_UNVERIFIED')


def prepare_contract(store, seed_ref, receipt_ref, proofs, exclude_namespace=None):
    seed = blob_json(store, seed_ref)
    if (seed.get('schema') != 'anipals-global-collector-seed-v1' or seed.get('partial') is not True
            or seed.get('globalComplete') is not False or seed.get('sourceScopeCount') != 6222):
        raise ValueError('EXACT_PARTIAL_GLOBAL_SEED_REQUIRED')
    verify_seed_receipt(seed, seed_ref, blob_json(store, receipt_ref))
    local = blob_json(store, seed['transferLedger']); diagnostic = blob_json(store, seed['diagnosticLedger'])['results']
    if any(type(c.get('body_bytes')) is not int or c['body_bytes'] < 0 for c in local):
        raise ValueError('INVALID_LOCAL_TRANSFER_LEDGER')
    if any(type(c.get('bodyBytes')) is not int or c['bodyBytes'] < 0 for c in diagnostic):
        raise ValueError('INVALID_DIAGNOSTIC_LEDGER')
    local_bytes, diagnostic_bytes = sum(c['body_bytes'] for c in local), sum(c['bodyBytes'] for c in diagnostic)
    if local_bytes != seed['localSourceBytes'] or diagnostic_bytes != seed['diagnosticSourceBytes']:
        raise ValueError('LOCAL_BUDGET_EVIDENCE_DIFFERS')
    budget = blob_json(store, seed['rangeBudget'])
    if budget['threeAttemptNetworkBudget'] != seed['sourceBudgetBytes']:
        raise ValueError('ORIGINAL_BUDGET_EVIDENCE_DIFFERS')
    if not proofs: raise ValueError('ACTUAL_CLOUD_PILOT_PROOF_REQUIRED')
    passed = [verify_pilot_proof(store, p, seed) for p in proofs]
    if any(p['collectorCode'] != passed[0]['collectorCode'] or p['appSourceSha'] != passed[0]['appSourceSha'] for p in passed):
        raise ValueError('PILOT_COLLECTOR_CODE_DIFFERS')
    ledgers = discover_spend(store, seed, exclude_namespace)
    if not set(p['namespace'] for p in passed).issubset({l['namespace'] for l in ledgers}):
        raise ValueError('PILOT_LEDGER_MISSING')
    charged = local_bytes+diagnostic_bytes+sum(l['additionalSourceBytes'] for l in ledgers)
    if charged >= seed['sourceBudgetBytes']: raise ValueError('GLOBAL_SOURCE_BUDGET_EXHAUSTED')
    return {'schema': 'anipals-global-collector-contract-v1', 'seed': seed_ref, 'seedArchiveReceipt': receipt_ref,
            'roster': seed['roster'], 'index': seed['index'], 'sourceScopeCount': 6222,
            'globalComplete': False, 'publicationAllowed': False, 'sourceBudgetBytes': seed['sourceBudgetBytes'],
            'collectorCode': passed[0]['collectorCode'], 'appSourceSha': passed[0]['appSourceSha'],
            'priorSourceBytes': charged, 'remainingSourceBytes': seed['sourceBudgetBytes']-charged,
            'localSourceBytes': local_bytes, 'diagnosticSourceBytes': diagnostic_bytes,
            'namespaceLedgers': ledgers, 'lineageSha256': digest(ledgers), 'verifiedPilotProofs': passed}


def assert_lineage_current(store, seed, contract, current_namespace=None):
    if digest(discover_spend(store, seed, current_namespace)) != contract['lineageSha256']:
        raise ValueError('GLOBAL_BUDGET_LINEAGE_CHANGED_REPLAN_REQUIRED')
