"""Read actual GitHub attempt evidence; archived JSON is never the trust entry point."""
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from cloud_execution import REPOSITORY
from cloud_full_contract import blob_json

API = 'https://api.github.com/repos/'+REPOSITORY


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args): return None


def request(url, token=None, redirect=False, limit=16_000_000):
    if token and not url.startswith(API+'/'): raise ValueError('GITHUB_TOKEN_DESTINATION_REJECTED')
    headers = {'User-Agent': 'anipals-private-collector', 'Accept': 'application/vnd.github+json'}
    if token: headers['Authorization'] = 'Bearer '+token
    opener = urllib.request.build_opener(NoRedirect())
    for attempt in range(3):
        try:
            try: response = opener.open(urllib.request.Request(url, headers=headers), timeout=30)
            except urllib.error.HTTPError as error:
                if redirect and error.code in (301, 302, 303, 307, 308):
                    location = error.headers.get('Location', ''); error.close(); return location
                raise
            with response:
                raw = response.read(limit+1)
            if len(raw) > limit: raise ValueError('GITHUB_EVIDENCE_SIZE_LIMIT')
            return raw
        except (OSError, TimeoutError) as error:
            if isinstance(error, urllib.error.HTTPError) and error.code < 500 and error.code != 429: raise
            if attempt == 2: raise OSError('GITHUB_EVIDENCE_RETRIEVAL_FAILED') from None
            time.sleep(2)


def actual_proof(store, result_ref, work):
    result = blob_json(store, result_ref); execution = result.get('execution', {})
    run_id, attempt = execution.get('runId'), execution.get('runAttempt')
    if (execution.get('kind') != 'github-actions' or execution.get('repository') != REPOSITORY
            or type(run_id) is not int or run_id < 1 or type(attempt) is not int or attempt < 1):
        raise ValueError('ACTUAL_GITHUB_ATTEMPT_REQUIRED')
    token = os.environ.get('GH_TOKEN')
    run_raw = request(f'{API}/actions/runs/{run_id}/attempts/{attempt}', token)
    run = json.loads(run_raw)
    if run.get('id') != run_id or run.get('run_attempt') != attempt:
        raise ValueError('GITHUB_ATTEMPT_RESPONSE_DIFFERS')
    # Only the actual pilot job can attest its final descriptor, never an uploaded log.
    jobs = json.loads(request(f'{API}/actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100', token))
    if jobs.get('total_count', 101) > 100: raise ValueError('GITHUB_JOBS_LIMIT')
    matching = [j for j in jobs.get('jobs', []) if j.get('name') == 'pilot' and j.get('conclusion') == 'success']
    if len(matching) != 1: raise ValueError('SUCCESSFUL_PILOT_JOB_REQUIRED')
    job = matching[0]
    if type(job.get('id')) is not int or job.get('run_id') != run_id or job.get('head_sha') != run.get('head_sha'):
        raise ValueError('PILOT_JOB_RUN_IDENTITY_DIFFERS')
    log = request(f'{API}/actions/jobs/{job["id"]}/logs', token, redirect=True)
    if isinstance(log, str):
        parsed = urllib.parse.urlsplit(log)
        if (parsed.scheme != 'https' or parsed.username or parsed.password or parsed.fragment
                or not parsed.hostname or not parsed.hostname.endswith(('.blob.core.windows.net', '.githubusercontent.com'))):
            raise ValueError('GITHUB_LOG_REDIRECT_REJECTED')
        log = request(log)  # The GitHub credential never follows the signed redirect.
    work.mkdir(parents=True, exist_ok=True)
    run_path, log_path = work/'run.json', work/'job.log'
    run_path.write_bytes(run_raw); log_path.write_bytes(log)
    return {'result': result_ref, 'githubRun': store.put(run_path), 'githubLog': store.put(log_path)}
