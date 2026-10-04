"""Immutable isolated native runtime request and bounded metadata mirror."""
import hashlib
from pathlib import Path
import re
from navigation_catalog_inputs import descriptor, exact, require, decode, checked_bytes, LocalObjects
from runtime_index_assembler import canonical_bytes
from navigation_catalog import plan as catalog_plan

SCHEMA = 'anipals-isolated-runtime-request-v1'
LIMITS = dict(metadata_bytes=2*1024**3, source_download_bytes=64*1024**3,
              archive_bytes=8*1024**3, output_bytes=8*1024**2)


def validate(value, source_sha):
    exact(value,'schema source_sha slug catalog catalog_request probe objects budgets','EXACT_ISOLATED_REQUEST_REQUIRED')
    require(value['schema'] == SCHEMA and value['source_sha'] == source_sha
            and re.fullmatch('[0-9a-f]{40}',source_sha), 'ISOLATED_SOURCE_SHA_REQUIRED')
    require(isinstance(value['slug'],str) and re.fullmatch('[a-z0-9]+(?:-[a-z0-9]+)*',value['slug']),
            'EXACT_ISOLATED_SLUG_REQUIRED')
    for name,maximum in [('catalog',32*1024**2),('catalog_request',2*1024**2),('probe',8192)]:
        descriptor(value[name],maximum)
    exact(value['budgets'],'metadata_bytes source_download_bytes archive_bytes output_bytes','EXACT_RESOURCE_BUDGETS_REQUIRED')
    for name,maximum in LIMITS.items():
        require(type(value['budgets'][name]) is int and 0 < value['budgets'][name] <= maximum,'RESOURCE_BUDGET_BOUNDS')
    require(value['budgets']['output_bytes'] >= 65536,'PRIVATE_OUTPUT_BUDGET_TOO_SMALL')
    rows = value['objects']
    require(isinstance(rows,list) and 1 <= len(rows) <= 1000,'COMPLETE_OBJECT_TREE_REQUIRED')
    refs = [value[n] for n in ('catalog','catalog_request','probe')] + rows
    seen = set()
    for ref in refs:
        exact(ref,'key sha256 bytes','EXACT_OBJECT_TREE_DESCRIPTOR_REQUIRED')
        # Exact native namespaces are checked by each shared catalog read before I/O.
        descriptor(ref,32*1024**2,ref['key'])
        key = ref['key'];sha = ref['sha256']
        require(isinstance(key,str) and len(key) <= 512,'OBJECT_KEY_BOUNDS')
        archive = 'archive/sha256/%s/%s'%(sha[:2],sha)
        namespace = r'navigation/(?:graphs/[a-z0-9-]+/[a-f0-9]{64}/manifests|migration-receipts/[a-f0-9]{40}/tiles-[a-zA-Z0-9.-]+/receipt-[a-z0-9-]+)/'+sha+r'\.json'
        require(key == archive or re.fullmatch(namespace,key),'PRIVATE_OBJECT_NAMESPACE_REFUSED')
        identity = (ref['key'],ref['sha256'])
        require(identity not in seen,'DUPLICATE_OBJECT_TREE_DESCRIPTOR'); seen.add(identity)
    require(sum(r['bytes'] for r in refs) <= value['budgets']['metadata_bytes'],'METADATA_BUDGET_EXCEEDED')
    return value


class LockedMirror:
    """Fetch only declared immutable inputs as requested by shared validators."""
    def __init__(self, store, root, request):
        self.store,self.root = store,Path(root)
        self.root.mkdir(mode=0o700)
        refs = [request[n] for n in ('catalog','catalog_request','probe')] + request['objects']
        self.allowed = {(r['key'],r['sha256']):r for r in refs}
        self.used = set()
        self.local = LocalObjects(self.root)

    def read(self,ref,maximum,key=None):
        descriptor(ref,maximum,key)
        identity = (ref['key'],ref['sha256'])
        require(self.allowed.get(identity) == ref,'UNDECLARED_INPUT_OBJECT')
        path = self.root/ref['sha256']
        if not path.exists() and not path.is_symlink():
            self.store.fetch(ref,path)
        self.used.add(identity)
        return self.local.read(ref,maximum,key)

    def complete(self):
        require(self.used == set(self.allowed),'UNUSED_OR_INCOMPLETE_OBJECT_TREE')


def verified_catalog(request, objects, repository):
    expected = objects.read(request['catalog'],32*1024**2)
    raw = objects.read(request['catalog_request'],2*1024**2)
    catalog = catalog_plan(raw,request['catalog_request']['sha256'],objects,repository)
    require(canonical_bytes(catalog) == canonical_bytes(decode(expected)),'CATALOG_DIFFERS_FROM_REVERIFIED_SOURCE_REQUEST')
    probe = objects.read(request['probe'],8192)
    objects.complete()
    require(request['slug'] in {r['slug'] for r in catalog['regions']},'SELECTED_REGION_NOT_IN_COMPLETE_SUPPLY')
    return catalog,probe


def private_blob(folder,raw):
    from plan_runtime_index import write_private
    sha = hashlib.sha256(raw).hexdigest();path = Path(folder)/sha
    if path.exists():
        checked_bytes(path.read_bytes(),sha,len(raw),32*1024**2)
    else:
        write_private(path,raw)
    return dict(key='archive/sha256/%s/%s'%(sha[:2],sha),sha256=sha,bytes=len(raw))
