"""Synthetic runtime runner descriptors/output; never cloud acceptance evidence."""
import hashlib
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'tools'),str(ROOT/'deploy')]
from native_runtime_contract import SCHEMA
from runtime_index_assembler import canonical_bytes


def blob(raw):
    sha = hashlib.sha256(raw).hexdigest()
    return dict(key='archive/sha256/%s/%s'%(sha[:2],sha),sha256=sha,bytes=len(raw))


def request():
    return dict(schema=SCHEMA,source_sha='a'*40,slug='europe-germany',catalog=blob(b'catalog'),
        catalog_request=blob(b'catalog-request'),probe=blob(b'probe'),objects=[blob(b'source')],
        budgets=dict(metadata_bytes=1024**2,source_download_bytes=1024**3,
                     archive_bytes=1024**3,output_bytes=1024**2))


def outputs(spec,ref,context):
    slug = spec['slug'];fp = 'b'*64
    identity = 'c'*64
    inventory = dict(schema='synthetic-inventory',target_identity_sha256=identity);inventory_sha = blob(canonical_bytes(inventory))['sha256']
    assembly = dict(mode='isolated-acceptance',selected_regions=[slug],catalog=spec['catalog'],
        catalog_request=spec['catalog_request'],target_inventory=dict(sha256=inventory_sha),target_identity_sha256=identity)
    assembly_sha = blob(canonical_bytes(assembly))['sha256']
    index = dict(schema=2,catalog_sha256=spec['catalog']['sha256'],local_coverage_sha256=blob(canonical_bytes({}))['sha256'],
        review=dict(mode='isolated-acceptance',selected_regions=[slug],request_sha256=assembly_sha,
                    target_inventory_sha256=inventory_sha,target_identity_sha256=identity,
                    runtime_activated=False,scope_routes_verified=False,production_supply_complete=False),
        storage_ownership={slug:dict(rollback=dict(storage='local',fingerprint=fp),selected=dict(storage='r2',fingerprint='d'*64))})
    digest = blob(canonical_bytes(index))['sha256']
    common = dict(execution=context,index_sha256=digest,production_activated=False,
                  production_supply_complete=False,published=False,catalog_sha256=spec['catalog']['sha256'])
    session = dict(common,event='ISOLATED_NATIVE_RUNTIME_SESSION_PASSED',restart_verified=True,
                   local_fingerprint=fp,remote_fingerprint='d'*64,scope_routes_verified=False,probe_sha256=spec['probe']['sha256'],request_sha256=assembly_sha)
    prep = dict(common,event='REAL_SOURCE_ISOLATED_CANDIDATE_PASSED',request_sha256=ref['sha256'],slug=slug,
        inventory_sha256=inventory_sha,source_budget_bytes=spec['budgets']['source_download_bytes'],source_bytes_reserved=100,source_bytes_received=50,
        local_native_verification=dict(verified=True,slug=slug,fingerprint=fp),source_plan=dict(fingerprint=fp))
    return dict(index=index,coverage={},inventory=inventory,session=session,preparation=prep,**{'assembly-request':assembly})


class Store:
    def __init__(self,mirror=None):
        self.mirror,self.saved,self.fetched,self.bucket = mirror,[],[],'fixture-private-bucket'
    def fetch(self,ref,path):
        self.fetched.append(ref)
        path.write_bytes((self.mirror/ref['sha256']).read_bytes())
    def save_json(self,prefix,value):
        self.saved.append((prefix,value));ref = blob(canonical_bytes(value))
        return dict(ref,key=prefix+ref['sha256']+'.json')
