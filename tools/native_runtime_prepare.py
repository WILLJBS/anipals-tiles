#!/usr/bin/env python3
"""Create a real same-run isolated local volume, assemble and test its R2 candidate."""
import argparse
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'tests'),str(ROOT/'deploy')]
from native_r2_acceptance import pinned_file, probe_payload
from native_runtime_acceptance import checked_execution, native_engine, main as acceptance_main
from native_runtime_contract import validate, verified_catalog, LockedMirror, private_blob
from native_runtime_source import SourceDownload, bounded_tiles
from navigation_catalog_inputs import decode, require, LocalObjects
from migration_contract import load_profile, validate_profile_supply
from regional_catalog import Catalog
from regional_download import prepare_region, activate_region
from regional_router import Router
from regional_storage import require_capacity
from regional_release import canonical_hash
from runtime_local_inventory import capture
from runtime_index_assembler import assemble, canonical_bytes
from plan_runtime_index import write_private


def materialize(request,catalog,objects,work,context,downloader):
    slug = request['slug'];profile = load_profile('original61',ROOT)
    require(slug in {r['slug'] for r in profile['roster']['region']},'BASELINE_LOCAL_REGION_REQUIRED')
    source = next(s for s in catalog['sources'] if s['registry'] == 'original61')
    release = decode(objects.read(source['release'],16*1024**2))
    ready = objects.read(source['ready'],4*1024**2)
    plans = validate_profile_supply(profile,release,ready)
    plan = next(p for p in plans if p['slug'] == slug)
    total = sum(p['size'] for p in plan['parts'])
    require(total <= request['budgets']['archive_bytes'],'SOURCE_ARCHIVE_BUDGET_EXCEEDED')
    require(total+len(plan['parts']) <= request['budgets']['source_download_bytes'],
            'SOURCE_DOWNLOAD_BUDGET_EXHAUSTED')
    volume = work/'volume';volume.mkdir(mode=0o700)  # No caller-supplied volume.
    require_capacity(volume,2*total,1024**3)
    local = prepare_region(volume,plan,downloader=downloader,reserve_bytes=1024**3)
    probes = decode((ROOT/'deploy/probes.json').read_bytes())
    with ExitStack() as stack:
        engine = native_engine(work/'local-native',stack)
        router = Router(Catalog(volume,profile['coverage']),engine,probes)
        proof = router.verify(slug,local['fingerprint'])
        activate_region(volume,local,proof)
    identity = canonical_hash(dict(execution=context,request_sha256=request['_request_sha'],
                                   device=volume.stat().st_dev,inode=volume.stat().st_ino))
    inventory = capture(volume,[slug],catalog['image'],identity)
    inventory_ref = private_blob(objects.root,canonical_bytes(inventory))
    return volume,identity,inventory,inventory_ref,proof,plan


def execute(request,request_sha,store,work,context):
    work = Path(work)
    require(work.resolve(strict=True) == work and work.is_dir()
            and not any(work.iterdir()),'FRESH_OWNED_WORK_DIRECTORY_REQUIRED')
    request = validate(request,context['runnerSourceSha'])
    require(store.bucket == os.environ.get('R2_BUCKET'),'PRIVATE_READER_BUCKET_DIFFERS')
    require_capacity(work,request['budgets']['metadata_bytes'],1024**3)
    objects = LockedMirror(store,work/'objects',request)
    catalog,probe_raw = verified_catalog(request,objects,ROOT)
    require(catalog['bucket'] == store.bucket,'R2_BUCKET_DIFFERS_FROM_REVERIFIED_CATALOG')
    row = next(r for r in catalog['regions'] if r['slug'] == request['slug'])
    payload = probe_payload(probe_raw,row['feature'],request['slug'])
    downloader = SourceDownload(request['budgets']['source_download_bytes'])
    spec = dict(request,_request_sha=request_sha)
    volume,identity,inventory,inventory_ref,proof,plan = materialize(spec,catalog,objects,work,context,downloader)
    assembly = dict(schema='anipals-runtime-index-request-v1',catalog=request['catalog'],
        catalog_request=request['catalog_request'],mode='isolated-acceptance',selected_regions=[request['slug']],
        probes={request['slug']:[dict(lat=p['lat'],lng=p['lon']) for p in payload['locations']]},
        target_inventory=inventory_ref,target_identity_sha256=identity,previous_index_sha256=None)
    raw = canonical_bytes(assembly);assembly_sha = hashlib.sha256(raw).hexdigest()
    candidate = assemble(raw,assembly_sha,LocalObjects(objects.root),volume,ROOT)
    output = work/'output';output.mkdir(mode=0o700)
    for name,value in [('assembly-request',assembly),('inventory',inventory),('index',candidate['index']),
                       ('coverage',candidate['coverage'])]:
        write_private(output/(name+'.json'),canonical_bytes(value))
    probe_path = work/'probe.json';write_private(probe_path,probe_raw)
    import regional_r2
    original = regional_r2.reader
    prefix = 'navigation/graphs/%s/%s/tiles/'%(request['slug'],row['graph_fingerprint'])
    manifest = decode(objects.local.read(row['manifest'],32*1024**2,row['manifest']['key']))
    def bounded_reader(*args,**kwargs):
        return bounded_tiles(original(*args,**kwargs),prefix,manifest['tiles'],downloader)
    previous_args = sys.argv
    try:
        regional_r2.reader = bounded_reader
        sys.argv = ['native_runtime_acceptance','--source-sha',request['source_sha'],
                    '--request',str(output/'assembly-request.json'),'--request-sha',assembly_sha,
                    '--objects',str(objects.root),'--volume',str(volume),
                    '--probe',str(probe_path),'--probe-sha',request['probe']['sha256'],
                    '--output',str(output/'session.json')]
        acceptance_main()
    finally:
        regional_r2.reader = original;sys.argv = previous_args
    session = decode((output/'session.json').read_bytes())
    require(session['restart_verified'] is True and session['index_sha256'] == candidate['index_sha256'],
            'ISOLATED_SESSION_DID_NOT_COMPLETE')
    proof_record = dict(schema=1,event='REAL_SOURCE_ISOLATED_CANDIDATE_PASSED',request_sha256=request_sha,
        execution=context,slug=request['slug'],local_native_verification=proof,source_plan=plan,
        source_bytes_reserved=downloader.reserved,source_bytes_received=downloader.actual,
        source_budget_bytes=downloader.budget,index_sha256=candidate['index_sha256'],
        inventory_sha256=inventory_ref['sha256'],catalog_sha256=request['catalog']['sha256'],
        production_activated=False,production_supply_complete=False,published=False)
    write_private(output/'preparation.json',canonical_bytes(proof_record))
    require(sum(p.stat().st_size for p in output.iterdir()) <= request['budgets']['output_bytes']//2,
            'PRIVATE_CANDIDATE_OUTPUT_BUDGET_EXCEEDED')
    return proof_record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('request','request-sha','source-sha','work'):
        parser.add_argument('--'+name,required=True)
    args = parser.parse_args();context = checked_execution(args.source_sha)
    raw = pinned_file(args.request,args.request_sha,2*1024**2)
    from cloud_collector_io import PrivateStore
    from private_archive_verify import archival_config
    from regional_r2 import connection
    import boto3
    base,bucket = connection();endpoint = base.meta.endpoint_url;base.close()
    client = boto3.client('s3',endpoint_url=endpoint,region_name='auto',config=archival_config(),
        aws_access_key_id=os.environ['R2_ACCESS_KEY_ID'],aws_secret_access_key=os.environ['R2_SECRET_ACCESS_KEY'])
    try:
        execute(decode(raw),args.request_sha,PrivateStore(client,bucket),Path(args.work),context)
    finally:
        client.close()


if __name__ == '__main__':
    try:main()
    except Exception as error:
        print(json.dumps(dict(event='ISOLATED_PREPARATION_FAILED',error=type(error).__name__)),file=sys.stderr)
        raise SystemExit(1) from None
