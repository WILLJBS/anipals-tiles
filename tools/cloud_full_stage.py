#!/usr/bin/env python3
"""Six finite collector stages; only verified Deadline checkpoints can continue."""
import argparse
import json
import os
from pathlib import Path
import re
import signal
import sys
from cloud_collect_full import execution, resume, validate_full
from cloud_full_runtime import load_code, remaining_seconds
from cloud_collect_places import Deadline
from cloud_full_contract import blob_json, namespace_spec
from cloud_collector_io import PrivateStore, Journal
from cloud_stage_policy import (MAX_STAGES, descriptor, load_result, checkpoint, current_matches,
                                continuation, complete_result, state_metrics)
from cloud_stage_proof import github_stage_proof
from private_archive_verify import revision, archival_config


def stage_context(source_sha, stage):
    if type(stage) is not int or not 1 <= stage <= MAX_STAGES:
        raise ValueError('FINITE_STAGE_REQUIRED')
    current = execution(source_sha)
    if os.environ.get('GITHUB_JOB') != f'stage{stage}':
        raise ValueError('STAGE_JOB_KEY_DIFFERS')
    return dict(current, stageIndex=stage, jobKey=f'stage{stage}')


def before_stage(store, spec, spec_sha, current, previous, proof=github_stage_proof):
    stage = current['stageIndex']
    if previous is None:
        if stage != 1: raise ValueError('PREVIOUS_STAGE_RECEIPT_REQUIRED')
        if list(store.list(f'archive/collector/{spec_sha}/')):
            raise ValueError('EXISTING_NAMESPACE_REQUIRES_TERMINAL_RECEIPT')
        return None
    result = load_result(store, previous, spec, spec_sha)
    state = checkpoint(store, result, spec_sha, spec)
    prior = result.get('execution', {})
    if stage > 1 and (prior.get('stageIndex') != stage-1 or prior.get('jobKey') != f'stage{stage-1}'
                      or prior.get('runnerSourceSha') != current['runnerSourceSha']):
        raise ValueError('IMMEDIATE_PREVIOUS_STAGE_REQUIRED')
    continuation(result, state, stage-1)
    proof(result, previous, current, bootstrap=stage == 1)
    return current_matches(store, state, spec, spec_sha)


def initial_progress(store, spec, spec_sha):
    journal = Journal(store, spec_sha, spec['sourceBudgetBytes'], spec['priorSourceBytes'])
    if journal.seq or journal.files or journal.ranges:
        return state_metrics(dict(seq=journal.seq, spent=journal.spent, pending=journal.pending,
                                  files=journal.files, ranges=journal.ranges))
    # A fresh attempt must not count inherited seed caches as new progress.
    contract = blob_json(store, spec['contract']); seed = blob_json(store, contract['seed'])
    files = {f['name']: f['blob'] for f in seed['completedSourceFiles']}
    ranges = {r['cacheKey']: r for r in seed['ranges']}
    for ledger in contract['namespaceLedgers']:
        prior = namespace_spec(store, ledger['namespace'])
        old = Journal(store, ledger['namespace'], prior['sourceBudgetBytes'], prior['priorSourceBytes'])
        ranges.update(old.ranges)
        if prior['mode'] == 'full': files.update(old.files)
    return dict(sequence=0, files=len(files), ranges=len(ranges), chargedBytes=spec['priorSourceBytes'])


def run_stage(store, spec, spec_sha, work, current, previous, runner=resume, proof=github_stage_proof):
    before_stage(store, spec, spec_sha, current, previous, proof)
    start = initial_progress(store, spec, spec_sha)
    context = dict(current, stageStartProgress=start, previousResult=previous)
    outcome = runner(store, spec, spec_sha, work, context)
    ref = descriptor(outcome.get('privateResult'))
    result = load_result(store, ref, spec, spec_sha)
    if result.get('execution') != context:
        raise ValueError('CURRENT_STAGE_EXECUTION_DIFFERS')
    state = checkpoint(store, result, spec_sha, spec)
    current_matches(store, state, spec, spec_sha)
    if result.get('complete') is True:
        complete_result(store, result, spec); status = 'complete'
    else:
        status = continuation(result, state, current['stageIndex'])
    return {'stageIndex': current['stageIndex'], 'jobKey': current['jobKey'], 'state': status, 'result': ref}


def finalize(store, spec, spec_sha, needs, current, proof=github_stage_proof):
    if set(needs) != {f'stage{i}' for i in range(1, MAX_STAGES+1)}:
        raise ValueError('FINAL_STAGE_SET_DIFFERS')
    previous = None; winner = None
    for i in range(1, MAX_STAGES+1):
        job = needs[f'stage{i}']; outputs = job.get('outputs', {})
        if winner is not None:
            if job.get('result') != 'skipped': raise ValueError('WORK_AFTER_COMPLETE')
            continue
        if job.get('result') != 'success': raise ValueError('FINAL_STAGE_FAILED_OR_CANCELLED')
        ref = descriptor(outputs.get('receipt', ''))
        result = load_result(store, ref, spec, spec_sha); execution_value = result.get('execution', {})
        if (execution_value.get('stageIndex') != i or execution_value.get('jobKey') != f'stage{i}'
                or execution_value.get('runnerSourceSha') != current['runnerSourceSha']
                or (i > 1 and execution_value.get('previousResult') != previous)):
            raise ValueError('FINAL_STAGE_CHAIN_DIFFERS')
        state = checkpoint(store, result, spec_sha, spec)
        proof(result, ref, current)
        if outputs.get('state') == 'complete':
            complete_result(store, result, spec)
            current_matches(store, state, spec, spec_sha); winner = ref
        elif outputs.get('state') == 'resume' and continuation(result, state, i) == 'resume':
            previous = ref
        else:
            raise ValueError('FINITE_STAGES_EXHAUSTED_OR_INVALID')
    if winner is None: raise ValueError('FINAL_COMPLETE_RESULT_REQUIRED')
    return winner


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('stage', 'finalize'), required=True)
    for name in ('source-sha', 'input-key', 'input-sha'):
        parser.add_argument('--'+name, required=True)
    parser.add_argument('--input-bytes', type=int, required=True)
    parser.add_argument('--stage', type=int)
    parser.add_argument('--previous-result', default='')
    parser.add_argument('--final-results', default='')
    args = parser.parse_args(); revision(args.source_sha)
    if (not re.fullmatch('[a-f0-9]{64}', args.input_sha)
            or args.input_key != f'archive/sha256/{args.input_sha[:2]}/{args.input_sha}'
            or not 0 < args.input_bytes < 1000000):
        raise ValueError('EXACT_PRIVATE_INPUT_REQUIRED')
    current = stage_context(args.source_sha, args.stage) if args.mode == 'stage' else execution(args.source_sha)
    if args.mode == 'stage':
        if 'COLLECTOR_STOP_AT' not in os.environ: raise ValueError('STAGE_TOTAL_DEADLINE_REQUIRED')
        def expire(*unused): raise Deadline()
        signal.signal(signal.SIGALRM, expire)
        signal.alarm(remaining_seconds({'maxMinutes': 330}))
    if args.mode == 'finalize' and os.environ.get('GITHUB_JOB') != 'finalize':
        raise ValueError('FINALIZER_JOB_KEY_DIFFERS')
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'deploy'))
    from regional_r2 import connection
    import boto3
    base, bucket = connection(); endpoint = base.meta.endpoint_url; base.close()
    client = boto3.client('s3', endpoint_url=endpoint, region_name='auto',
        aws_access_key_id=os.environ['R2_ACCESS_KEY_ID'], aws_secret_access_key=os.environ['R2_SECRET_ACCESS_KEY'],
        config=archival_config())
    store = PrivateStore(client, bucket); work = Path('.local/private-full-stage'); work.mkdir(parents=True, exist_ok=True)
    try:
        spec = store.json(args.input_key, args.input_sha, args.input_bytes)
        validate_full(spec)
        load_code(store, spec, work)
        if args.mode == 'stage':
            marker = run_stage(store, spec, args.input_sha, work, current,
                               descriptor(args.previous_result) if args.previous_result else None)
            print(json.dumps({'collectorStage': marker}), flush=True)
            with open(os.environ['GITHUB_OUTPUT'], 'a') as target:
                target.write('state='+marker['state']+'\nreceipt='+json.dumps(marker['result'], separators=(',', ':'))+'\n')
        else:
            ref = finalize(store, spec, args.input_sha, json.loads(args.final_results), current)
            print(json.dumps({'collectorComplete': True, 'privateResult': ref, 'sourceScopes': 6222}), flush=True)
    finally:
        client.close()


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # No SDK exception strings, source coordinates or raw payloads in logs.
        code = str(error) if re.fullmatch('[A-Z0-9_]{1,80}', str(error)) else 'UNEXPECTED_STAGE_FAILURE'
        print(json.dumps({'collectorStageFailed': True, 'errorType': type(error).__name__, 'code': code}), file=sys.stderr)
        raise SystemExit(1) from None
