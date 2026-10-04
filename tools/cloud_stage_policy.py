"""Fail-closed continuation policy over the existing full collector namespace."""
import json
import re
from cloud_full_contract import blob_json
from cloud_collector_io import Journal

MAX_STAGES = 6


def descriptor(raw):
    value = json.loads(raw) if isinstance(raw, str) else raw
    if (not isinstance(value, dict) or set(value) != {'key', 'sha256', 'bytes'}
            or not re.fullmatch('[a-f0-9]{64}', str(value.get('sha256')))
            or type(value.get('bytes')) is not int or not 0 < value['bytes'] < 16000000):
        raise ValueError('EXACT_STAGE_RESULT_DESCRIPTOR_REQUIRED')
    return value


def load_result(store, ref, spec, spec_sha):
    ref = descriptor(ref)
    if ref['key'] != f'archive/collector/{spec_sha}/results/{ref["sha256"]}.json':
        raise ValueError('STAGE_RESULT_NAMESPACE_DIFFERS')
    result = blob_json(store, ref)
    if (result.get('schema') != 'anipals-cloud-collector-result-v1'
            or result.get('specSha256') != spec_sha or result.get('sourceScopeCount') != 6222
            or result.get('publication') != 'pending' or result.get('navigationTargetsPublished') != 0
            or result.get('budgetBytes') != spec['sourceBudgetBytes']
            or result.get('priorSourceBytes') != spec['priorSourceBytes']
            or result.get('appSourceSha') != spec['appSourceSha']
            or result.get('roster') != spec['roster'] or result.get('index') != spec['index']):
        raise ValueError('STAGE_RESULT_CONTRACT_DIFFERS')
    contract = blob_json(store, spec['contract'])
    if (not re.fullmatch('[a-f0-9]{64}', str(contract.get('lineageSha256')))
            or result.get('budgetLedger', {}).get('lineageSha256') != contract['lineageSha256']):
        raise ValueError('STAGE_BUDGET_LINEAGE_DIFFERS')
    return result


def state_metrics(state):
    return {'sequence': state['seq'], 'files': len(state['files']), 'ranges': len(state['ranges']),
            'chargedBytes': state['spent'] + sum(state['pending'].values())}


def checkpoint(store, result, spec_sha, spec):
    ref = result.get('checkpoint', {})
    if not ref.get('key', '').startswith(f'archive/collector/{spec_sha}/snapshots/'):
        raise ValueError('STAGE_CHECKPOINT_NAMESPACE_DIFFERS')
    state = blob_json(store, ref); metrics = state_metrics(state); ledger = result.get('budgetLedger', {})
    if (metrics['sequence'] != ledger.get('checkpointSequence')
            or metrics['chargedBytes'] != result.get('sourceBytesCharged')
            or sum(state['pending'].values()) != ledger.get('unsettledReservedBytes')
            or ledger.get('namespace') != spec_sha or ledger.get('scope') != 'global-lineage'
            or not spec['priorSourceBytes'] <= metrics['chargedBytes'] <= spec['sourceBudgetBytes']):
        raise ValueError('STAGE_CHECKPOINT_BUDGET_DIFFERS')
    return state


def current_matches(store, state, spec, spec_sha):
    live = Journal(store, spec_sha, spec['sourceBudgetBytes'], spec['priorSourceBytes'])
    actual = dict(seq=live.seq, spent=live.spent, pending=live.pending, files=live.files, ranges=live.ranges)
    if state != actual: raise ValueError('STAGE_LEDGER_ADVANCED_OR_CHANGED')
    return state_metrics(actual)


def continuation(result, state, stage):
    if result.get('complete') is not False or result.get('failureType') != 'Deadline':
        raise ValueError('ONLY_EXPLICIT_DEADLINE_CAN_CONTINUE')
    before = result.get('execution', {}).get('stageStartProgress')
    after = state_metrics(state)
    if before is not None:
        if (after['files'] < before['files'] or after['ranges'] < before['ranges']
                or after['chargedBytes'] < before['chargedBytes']
                or (after['files'] == before['files'] and after['ranges'] == before['ranges'])):
            raise ValueError('STAGE_NO_DURABLE_PROGRESS')
    if after['chargedBytes'] >= result['budgetBytes']: raise ValueError('STAGE_BUDGET_EXHAUSTED')
    return 'resume' if stage < MAX_STAGES else 'exhausted'


def complete_result(store, result, spec):
    if result.get('complete') is not True or result.get('failureType') is not None:
        raise ValueError('FINAL_COMPLETE_RESULT_REQUIRED')
    contract = blob_json(store, spec['contract']); seed = blob_json(store, contract['seed'])
    state = blob_json(store, result['checkpoint'])
    summary = blob_json(store, result.get('outputs', {}).get('summary.json', {}))
    if (len(state['files']) != seed['sourceFilesTotal']
            or summary.get('filesCompleted') != seed['sourceFilesTotal']
            or summary.get('filesTotal') != seed['sourceFilesTotal']
            or result.get('filesCompleted') != seed['sourceFilesTotal']
            or summary.get('citiesRequested') != 6222
            or len(summary.get('cityComplete', {})) != 6222
            or not all(v is True for v in summary['cityComplete'].values())
            or summary.get('failures') != [] or summary.get('geometryLookupFailures') != []
            or result.get('candidateCount') != summary.get('uniqueNamedCandidates')
            or type(result.get('candidateCount')) is not int):
        raise ValueError('FINAL_GLOBAL_SOURCE_COMPLETENESS_DIFFERS')
    roster = blob_json(store, spec['roster'])
    from city_scope import city_regions, city_source_rows
    if set(summary['cityComplete']) != set(city_regions(city_source_rows(roster))):
        raise ValueError('FINAL_SOURCE_SCOPE_IDENTITIES_DIFFER')
    if result['budgetLedger']['unsettledReservedBytes'] != 0:
        raise ValueError('FINAL_UNSETTLED_RESERVATIONS')
