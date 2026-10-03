"""CI identity from GitHub-owned environment; full rollout also needs external run proof."""
import os
import re

REPOSITORY = 'WILLJBS/anipals-tiles'
WORKFLOW = '.github/workflows/collect-private-places.yml'


def github_execution(source_sha, env=None):
    env = os.environ if env is None else env
    ref = env.get('GITHUB_REF', '')
    if (env.get('GITHUB_ACTIONS') != 'true' or env.get('GITHUB_REPOSITORY') != REPOSITORY
            or not re.fullmatch('[a-f0-9]{40}', source_sha)
            or env.get('GITHUB_SHA') != source_sha or env.get('GITHUB_WORKFLOW_SHA') != source_sha
            or not re.fullmatch(r'refs/(heads|tags)/[^\s]+', ref)
            or env.get('GITHUB_WORKFLOW_REF') != f'{REPOSITORY}/{WORKFLOW}@{ref}'
            or not re.fullmatch('[1-9][0-9]{0,19}', env.get('GITHUB_RUN_ID', ''))
            or not re.fullmatch('[1-9][0-9]{0,8}', env.get('GITHUB_RUN_ATTEMPT', ''))):
        raise ValueError('TRUSTED_GITHUB_EXECUTION_CONTEXT_REQUIRED')
    return {'kind': 'github-actions', 'repository': REPOSITORY, 'workflowPath': WORKFLOW,
            'runId': int(env['GITHUB_RUN_ID']), 'runAttempt': int(env['GITHUB_RUN_ATTEMPT']),
            'runnerSourceSha': source_sha, 'workflowSha': env['GITHUB_WORKFLOW_SHA'], 'ref': ref}
