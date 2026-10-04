"""Read GitHub-owned job evidence for an exact private collector stage result."""
import json
import os
import urllib.parse
from cloud_full_github import API, request
from cloud_collect_full import WORKFLOW
from cloud_execution import REPOSITORY


def github_stage_proof(result, descriptor, current, bootstrap=False, fetch=request):
    execution = result['execution']; run_id = execution.get('runId'); attempt = execution.get('runAttempt')
    if (execution.get('kind') != 'github-actions' or execution.get('repository') != REPOSITORY
            or execution.get('workflowPath') != WORKFLOW or type(run_id) is not int
            or type(attempt) is not int or run_id < 1 or attempt < 1):
        raise ValueError('STAGE_GITHUB_IDENTITY_REQUIRED')
    if bootstrap:
        if run_id == current['runId']: raise ValueError('BOOTSTRAP_MUST_PRECEDE_CURRENT_RUN')
    elif (run_id, attempt) != (current['runId'], current['runAttempt']):
        raise ValueError('STAGE_RUN_ATTEMPT_DIFFERS')
    token = os.environ.get('GH_TOKEN')
    run = json.loads(fetch(f'{API}/actions/runs/{run_id}/attempts/{attempt}', token))
    if (run.get('id') != run_id or run.get('run_attempt') != attempt
            or run.get('head_sha') != execution.get('runnerSourceSha')
            or execution.get('workflowSha') != execution.get('runnerSourceSha')
            or run.get('path') != WORKFLOW or run.get('event') != 'workflow_dispatch'
            or run.get('repository', {}).get('full_name') != REPOSITORY):
        raise ValueError('STAGE_ACTUAL_RUN_DIFFERS')
    if bootstrap and (run.get('status') != 'completed' or run.get('conclusion') != 'failure'):
        raise ValueError('BOOTSTRAP_TERMINAL_FAILED_RUN_REQUIRED')
    if not bootstrap and run.get('conclusion') in ('cancelled', 'timed_out'):
        raise ValueError('STAGE_RUN_CANCELLED')
    jobs = json.loads(fetch(f'{API}/actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100', token))
    if jobs.get('total_count', 101) > 100: raise ValueError('STAGE_JOBS_LIMIT')
    legacy = 'stageIndex' not in execution
    if legacy and not bootstrap: raise ValueError('LEGACY_RESULT_ONLY_FOR_BOOTSTRAP')
    if not legacy and (type(execution.get('stageIndex')) is not int
                       or not 1 <= execution['stageIndex'] <= 6
                       or execution.get('jobKey') != f'stage{execution["stageIndex"]}'):
        raise ValueError('STAGE_JOB_IDENTITY_DIFFERS')
    name = 'full' if legacy else f'collector-stage-{execution["stageIndex"]}'
    candidates = [j for j in jobs.get('jobs', []) if j.get('name') == name]
    if len(candidates) != 1: raise ValueError('UNIQUE_ACTUAL_STAGE_JOB_REQUIRED')
    job = candidates[0]
    expected = 'failure' if legacy else 'success'
    if (job.get('status') != 'completed' or job.get('conclusion') != expected
            or job.get('run_id') != run_id or job.get('head_sha') != run['head_sha']
            or type(job.get('id')) is not int):
        raise ValueError('ACTUAL_STAGE_JOB_NOT_COMPLETE')
    log = fetch(f'{API}/actions/jobs/{job["id"]}/logs', token, redirect=True)
    if isinstance(log, str):
        parsed = urllib.parse.urlsplit(log)
        if (parsed.scheme != 'https' or parsed.username or parsed.password or parsed.fragment
                or not parsed.hostname or not parsed.hostname.endswith(('.blob.core.windows.net', '.githubusercontent.com'))):
            raise ValueError('STAGE_LOG_REDIRECT_REJECTED')
        log = fetch(log)
    matched = False
    for line in log.decode('utf8').splitlines():
        start = line.find('{')
        if start < 0: continue
        try: value = json.loads(line[start:])
        except json.JSONDecodeError: continue
        if legacy:
            matched |= value.get('privateResult') == descriptor and value.get('complete') is False
        else:
            marker = value.get('collectorStage', {})
            matched |= (marker.get('result') == descriptor
                        and marker.get('stageIndex') == execution['stageIndex']
                        and marker.get('jobKey') == execution.get('jobKey')
                        and marker.get('state') in ('resume', 'complete', 'exhausted'))
    if not matched: raise ValueError('STAGE_RESULT_NOT_IN_ACTUAL_JOB_LOG')
    return {'runId': run_id, 'runAttempt': attempt, 'jobId': job['id'], 'headSha': run['head_sha']}
