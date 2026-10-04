"""Strict private request contract for one real R2/native route acceptance."""
import os
import re
from cloud_execution import REPOSITORY

WORKFLOW = '.github/workflows/native-r2-acceptance.yml'
SCHEMA = 'anipals-native-r2-request-v1'
SHA = re.compile('[a-f0-9]{64}')
REVISION = re.compile('[a-f0-9]{40}')
LIMITS = {'release': 16*1024*1024, 'ready': 4*1024*1024, 'probe': 8192,
          'receipt': 8*1024*1024, 'manifest': 32*1024*1024}


def check(ok, code):
    if not ok:
        raise ValueError(code)


def descriptor(value, maximum, key=None):
    check(isinstance(value, dict) and set(value) == {'key', 'sha256', 'bytes'}, 'EXACT_PRIVATE_DESCRIPTOR_REQUIRED')
    sha, size = value['sha256'], value['bytes']
    check(isinstance(sha, str) and SHA.fullmatch(sha)
          and type(size) is int and 0 < size <= maximum, 'PRIVATE_DESCRIPTOR_BOUNDS')
    expected = f'archive/sha256/{sha[:2]}/{sha}' if key is None else key
    check(value['key'] == expected, 'PRIVATE_KEY_NOT_ALLOWED')
    return value


def validate_request(value, source_sha):
    fields = {'schema', 'diagnosticSha', 'migrationSha', 'contract', 'tag', 'slug',
              'release', 'ready', 'probe', 'receipt', 'manifest', 'graphFingerprint'}
    check(isinstance(value, dict) and set(value) == fields and value['schema'] == SCHEMA,
          'EXACT_NATIVE_REQUEST_REQUIRED')
    check(isinstance(source_sha, str) and REVISION.fullmatch(source_sha)
          and value['diagnosticSha'] == source_sha
          and isinstance(value['migrationSha'], str) and REVISION.fullmatch(value['migrationSha']),
          'REQUEST_REVIEWED_REVISION_REQUIRED')
    check(value['contract'] in ('original61', 'gap3', 'additions129')
          and isinstance(value['tag'], str) and re.fullmatch('tiles-[a-zA-Z0-9.-]{1,100}', value['tag'])
          and isinstance(value['slug'], str) and re.fullmatch('[a-z0-9]+(?:-[a-z0-9]+)*', value['slug'])
          and len(value['slug']) <= 100 and isinstance(value['graphFingerprint'], str)
          and SHA.fullmatch(value['graphFingerprint']), 'REGISTERED_REGION_IDENTITY_REQUIRED')
    for name in ('release', 'ready', 'probe'):
        descriptor(value[name], LIMITS[name])
    for name in ('receipt', 'manifest'):
        # Validate descriptor shape/hash before interpolating the exact namespace.
        d = value[name]
        check(isinstance(d, dict) and isinstance(d.get('sha256'), str) and SHA.fullmatch(d['sha256']),
              'PRIVATE_DESCRIPTOR_HASH_REQUIRED')
        if name == 'receipt':
            key = ('navigation/migration-receipts/%s/%s/receipt-%s/%s.json' %
                   (value['migrationSha'], value['tag'], value['slug'], d['sha256']))
        else:
            key = 'navigation/graphs/%s/%s/manifests/%s.json' % (value['slug'], value['graphFingerprint'], d['sha256'])
        descriptor(d, LIMITS[name], key)
    return value


def execution(source_sha, env=None):
    env = os.environ if env is None else env
    ref = env.get('GITHUB_REF', '')
    check(env.get('GITHUB_ACTIONS') == 'true' and env.get('GITHUB_REPOSITORY') == REPOSITORY
          and env.get('GITHUB_SHA') == source_sha and env.get('GITHUB_WORKFLOW_SHA') == source_sha
          and re.fullmatch(r'refs/(heads|tags)/[^\s]+', ref)
          and env.get('GITHUB_WORKFLOW_REF') == f'{REPOSITORY}/{WORKFLOW}@{ref}'
          and re.fullmatch('[1-9][0-9]{0,19}', env.get('GITHUB_RUN_ID', ''))
          and re.fullmatch('[1-9][0-9]{0,8}', env.get('GITHUB_RUN_ATTEMPT', '')),
          'TRUSTED_NATIVE_GITHUB_CONTEXT_REQUIRED')
    return dict(kind='github-actions', repository=REPOSITORY, workflowPath=WORKFLOW,
                runId=int(env['GITHUB_RUN_ID']), runAttempt=int(env['GITHUB_RUN_ATTEMPT']),
                runnerSourceSha=source_sha, workflowSha=source_sha, ref=ref)


def native_args(value):
    """Only fixed flags and validated values; never user commands or arbitrary paths."""
    result = ['/diagnostic/native_r2_acceptance.py', '--checkout', '/work',
              '--diagnostic-sha', value['diagnosticSha'], '--migration-sha', value['migrationSha'],
              '--contract', value['contract'], '--tag', value['tag'], '--slug', value['slug']]
    for name, file in [('release', 'release.json'), ('ready', 'READY'), ('probe', 'probe.json')]:
        result += ['--'+name, '/input/'+file, '--'+name+'-sha', value[name]['sha256']]
    for name in ('receipt', 'manifest'):
        result += ['--'+name+'-sha', value[name]['sha256'], '--'+name+'-size', str(value[name]['bytes'])]
    return result + ['--graph-fingerprint', value['graphFingerprint'], '--output', '/output/result.json']
