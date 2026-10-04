#!/usr/bin/env python3
"""Explicit CI-owned isolated runtime acceptance; never mount a production volume."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid
from cloud_collector_io import PrivateStore
from native_r2_ci import run
from native_r2_contract import descriptor,execution,check
from native_runtime_contract import validate
from navigation_catalog_inputs import decode
from private_archive_verify import archival_config,revision
from runtime_index_assembler import canonical_bytes
from plan_runtime_index import write_private

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ('assembly-request','inventory','index','coverage','session','preparation')


def docker_command(request,ref,work,name):
    argv = ['docker','run','--name',name,'--memory','1536m','--cpus','2',
            '--user','%s:%s'%(os.getuid(),os.getgid()),'--entrypoint','python3']
    forwarded = ('R2_ENDPOINT_URL','R2_BUCKET','R2_ACCESS_KEY_ID','R2_SECRET_ACCESS_KEY',
        'GITHUB_ACTIONS','GITHUB_REPOSITORY','GITHUB_SHA','GITHUB_WORKFLOW_SHA','GITHUB_REF',
        'GITHUB_WORKFLOW_REF','GITHUB_RUN_ID','GITHUB_RUN_ATTEMPT')
    for key in forwarded:argv += ['-e',key]
    for value in ('PYTHONDONTWRITEBYTECODE=1','PYTHONPATH=/opt/diagnostic-sdk',
                  'GIT_CONFIG_COUNT=1','GIT_CONFIG_KEY_0=safe.directory','GIT_CONFIG_VALUE_0=/work',
                  'RUNNER_TEMP=/isolated'):
        argv += ['-e',value]
    for source,target,readonly in [(ROOT,'/work',True),(work/'input','/input',True),(work/'runtime','/isolated',False)]:
        argv += ['--mount','type=bind,src=%s,dst=%s%s'%(source,target,',readonly' if readonly else '')]
    return argv+['anipals-native-runtime:'+request['source_sha'],'/work/tools/native_runtime_prepare.py',
        '--request','/input/request.json','--request-sha',ref['sha256'],
        '--source-sha',request['source_sha'],'--work','/isolated']


def checked_outputs(folder,request,ref,context):
    check(folder.is_dir() and not folder.is_symlink(),'PRIVATE_OUTPUT_DIRECTORY_MISSING')
    check({p.name for p in folder.iterdir()} == {name+'.json' for name in ARTIFACTS},'EXACT_PRIVATE_OUTPUT_SET_REQUIRED')
    total = 0;values = {}
    for name in ARTIFACTS:
        path = folder/(name+'.json');size = path.stat().st_size;total += size
        check(path.is_file() and not path.is_symlink() and 0 < size <= request['budgets']['output_bytes']//2,
              'PRIVATE_OUTPUT_BOUNDS')
        check(total <= request['budgets']['output_bytes']//2,'PRIVATE_OUTPUT_BUDGET_EXCEEDED')
        values[name] = decode(path.read_bytes())
    preparation,session,index = (values[n] for n in ('preparation','session','index'))
    digest = hashlib.sha256(canonical_bytes(index)).hexdigest()
    check(preparation.get('event') == 'REAL_SOURCE_ISOLATED_CANDIDATE_PASSED'
          and preparation.get('request_sha256') == ref['sha256'] and preparation.get('execution') == context
          and preparation.get('slug') == request['slug'] and preparation.get('index_sha256') == digest
          and preparation.get('inventory_sha256') == hashlib.sha256(canonical_bytes(values['inventory'])).hexdigest()
          and preparation.get('catalog_sha256') == request['catalog']['sha256']
          and preparation.get('source_budget_bytes') == request['budgets']['source_download_bytes']
          and type(preparation.get('source_bytes_reserved')) is int
          and 0 < preparation['source_bytes_reserved'] <= preparation['source_budget_bytes'],
          'PREPARATION_RESULT_IDENTITY_DIFFERS')
    check(session.get('event') == 'ISOLATED_NATIVE_RUNTIME_SESSION_PASSED'
          and session.get('execution') == context and session.get('restart_verified') is True
          and session.get('index_sha256') == digest and session.get('probe_sha256') == request['probe']['sha256']
          and session.get('request_sha256') == hashlib.sha256(canonical_bytes(values['assembly-request'])).hexdigest()
          and session.get('catalog_sha256') == request['catalog']['sha256'],'SESSION_RESULT_IDENTITY_DIFFERS')
    for value in (preparation,session):
        check(value.get('production_activated') is False and value.get('production_supply_complete') is False
              and value.get('published') is False,'PRODUCTION_COMPLETION_CLAIM_REFUSED')
    check(index.get('schema') == 2 and index.get('catalog_sha256') == request['catalog']['sha256']
          and index.get('review',{}).get('mode') == 'isolated-acceptance'
          and index['review'].get('selected_regions') == [request['slug']],'ISOLATED_INDEX_SCOPE_DIFFERS')
    assembly,inventory = values['assembly-request'],values['inventory']
    inventory_sha = hashlib.sha256(canonical_bytes(inventory)).hexdigest()
    assembly_sha = hashlib.sha256(canonical_bytes(assembly)).hexdigest()
    proof = preparation.get('local_native_verification',{})
    check(assembly.get('mode') == 'isolated-acceptance' and assembly.get('selected_regions') == [request['slug']]
          and assembly.get('catalog') == request['catalog'] and assembly.get('catalog_request') == request['catalog_request']
          and assembly.get('target_inventory',{}).get('sha256') == inventory_sha
          and index['review'].get('request_sha256') == assembly_sha
          and index['review'].get('target_inventory_sha256') == inventory_sha
          and proof.get('verified') is True and proof.get('slug') == request['slug']
          and proof.get('fingerprint') == session.get('local_fingerprint')
          and preparation.get('source_plan',{}).get('fingerprint') == session.get('local_fingerprint')
          and index.get('storage_ownership',{}).get(request['slug'],{}).get('rollback') ==
              dict(storage='local',fingerprint=session.get('local_fingerprint')),
          'SOURCE_INVENTORY_INDEX_SESSION_CHAIN_DIFFERS')
    identity = assembly.get('target_identity_sha256')
    check(isinstance(identity,str) and len(identity) == 64
          and identity == inventory.get('target_identity_sha256') == index['review'].get('target_identity_sha256')
          and index.get('local_coverage_sha256') == hashlib.sha256(canonical_bytes(values['coverage'])).hexdigest(),
          'TARGET_IDENTITY_OR_COVERAGE_CHAIN_DIFFERS')
    check(session.get('scope_routes_verified') is False
          and all(index['review'].get(key) is False for key in
                  ('runtime_activated','scope_routes_verified','production_supply_complete'))
          and index['storage_ownership'][request['slug']].get('selected') ==
              dict(storage='r2',fingerprint=session.get('remote_fingerprint')),
          'ISOLATED_COMPLETION_OR_REMOTE_OWNER_DIFFERS')
    check(type(preparation.get('source_bytes_received')) is int
          and 0 < preparation['source_bytes_received'] <= preparation['source_bytes_reserved'],
          'SOURCE_BYTE_ACCOUNTING_DIFFERS')
    return values


class OutputBudget:
    def __init__(self,store,maximum):self.store,self.maximum,self.used = store,maximum,0
    def save(self,prefix,value):
        size = len(canonical_bytes(value))
        check(size <= self.maximum-self.used,'PRIVATE_OUTPUT_BUDGET_EXHAUSTED')
        self.used += size
        return self.store.save_json(prefix,value)


def execute(store,request,ref,context,work,runner=run):
    request = validate(request,context['runnerSourceSha'])
    check(work.is_dir() and not any(work.iterdir()),'FRESH_CI_DIRECTORY_REQUIRED')
    (work/'input').mkdir(mode=0o700);(work/'runtime').mkdir(mode=0o700)
    write_private(work/'input/request.json',canonical_bytes(request))
    check(hashlib.sha256(canonical_bytes(request)).hexdigest() == ref['sha256'],
          'REQUEST_MUST_BE_CANONICAL_IMMUTABLE_JSON')
    log = work/'private.log';log.touch(mode=0o600)
    phase,error,values = 'candidate-build',None,None
    name = 'anipals-runtime-'+uuid.uuid4().hex
    try:
        image = (ROOT/'deploy/valhalla-image.txt').read_text().strip()
        base = 'anipals-native-base:'+request['source_sha']
        runner(['docker','build','-f','deploy/Dockerfile','--build-arg','VALHALLA_IMAGE='+image,
                '--build-arg','REVISION='+request['source_sha'],'-t',base,'.'],log,600)
        phase = 'diagnostic-build'
        runner(['docker','build','-f','tests/Dockerfile.native-r2','--build-arg','CANDIDATE_IMAGE='+base,
                '-t','anipals-native-runtime:'+request['source_sha'],'.'],log,300)
        phase = 'source-materialization-and-runtime-session'
        try:runner(docker_command(request,ref,work,name),log,2100)
        finally:runner(['docker','rm','-f',name],log,30)
        phase = 'verify-private-output'
        values = checked_outputs(work/'runtime/output',request,ref,context)
        phase = 'complete'
    except Exception as failure:
        error = type(failure).__name__
    prefix = 'navigation/isolated-runtime-acceptance/'+ref['sha256']+'/'
    budget = OutputBudget(store,request['budgets']['output_bytes'])
    artifacts = {}
    if error is None:
        try:
            for name,value in values.items():artifacts[name] = budget.save(prefix+'candidate/'+name+'/',value)
        except Exception as failure:
            error = type(failure).__name__;phase = 'private-output-upload'
    size = log.stat().st_size
    with log.open('rb') as stream:
        bound = min(64*1024,request['budgets']['output_bytes']//64)
        stream.seek(max(0,size-bound));tail = stream.read(bound).decode('utf8',errors='replace')
    log_ref = budget.save(prefix+'logs/',dict(schema=1,totalBytes=size,truncated=size>bound,text=tail))
    result = dict(schema='anipals-isolated-runtime-result-v1',complete=error is None,
        request=ref,execution=context,phase=phase,error=error,privateArtifacts=artifacts,privateLog=log_ref,
        productionActivated=False,productionSupplyComplete=False,published=False)
    result_ref = budget.save(prefix+'results/',result)
    return dict(complete=error is None,privateResult=result_ref,productionActivated=False,published=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source-sha','input-key','input-sha'):parser.add_argument('--'+name,required=True)
    parser.add_argument('--input-bytes',type=int,required=True)
    args = parser.parse_args();revision(args.source_sha);context = execution(args.source_sha)
    check(not subprocess.check_output(['git','status','--porcelain'],cwd=ROOT),'CLEAN_REVIEWED_CHECKOUT_REQUIRED')
    ref = descriptor(dict(key=args.input_key,sha256=args.input_sha,bytes=args.input_bytes),2*1024**2)
    temp_root = Path(os.environ['RUNNER_TEMP'])
    check(temp_root.is_absolute() and temp_root.resolve(strict=True) == temp_root,'TRUSTED_RUNNER_TEMP_REQUIRED')
    sys.path.insert(0,str(ROOT/'deploy'))
    from regional_r2 import connection
    import boto3
    base,bucket = connection();endpoint = base.meta.endpoint_url;base.close()
    client = boto3.client('s3',endpoint_url=endpoint,region_name='auto',config=archival_config(),
        aws_access_key_id=os.environ['R2_ACCESS_KEY_ID'],aws_secret_access_key=os.environ['R2_SECRET_ACCESS_KEY'])
    try:
        store = PrivateStore(client,bucket)
        request = validate(store.json(ref['key'],ref['sha256'],ref['bytes']),args.source_sha)
        with tempfile.TemporaryDirectory(prefix='anipals-runtime-',dir=temp_root) as folder:
            result = execute(store,request,ref,context,Path(folder))
    finally:client.close()
    print(json.dumps(result),flush=True)
    if not result['complete']:raise SystemExit(2)


if __name__ == '__main__':
    try:main()
    except Exception as error:
        print(json.dumps(dict(event='ISOLATED_RUNTIME_CI_FAILED',error=type(error).__name__)),file=sys.stderr)
        raise SystemExit(1) from None
