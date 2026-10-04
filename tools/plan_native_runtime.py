#!/usr/bin/env python3
"""Create an offline canonical private isolated request from a complete source tree."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
from migration_contract import ROOT,load_profile,source_revision
from navigation_catalog import plan as catalog_plan
from navigation_catalog_inputs import LocalObjects,decode,require
from native_runtime_contract import SCHEMA,validate
from runtime_index_assembler import canonical_bytes
from plan_runtime_index import write_private


class RecordedObjects:
    def __init__(self,objects):self.objects,self.refs=objects,{}
    def read(self,ref,maximum,key=None):
        raw=self.objects.read(ref,maximum,key)
        self.refs[(ref['key'],ref['sha256'])]=ref
        return raw


def plan(source_sha,catalog_ref,source_ref,probe_ref,objects,slug,budgets,root=ROOT):
    mirror=RecordedObjects(objects)
    catalog_raw=mirror.read(catalog_ref,32*1024**2)
    source_raw=mirror.read(source_ref,2*1024**2)
    catalog=catalog_plan(source_raw,source_ref['sha256'],mirror,root)
    require(canonical_bytes(catalog)==canonical_bytes(decode(catalog_raw)),
            'CATALOG_DIFFERS_FROM_REVERIFIED_SOURCE_REQUEST')
    baseline=load_profile('original61',root)
    require(slug in {r['slug'] for r in baseline['roster']['region']},'BASELINE_LOCAL_REGION_REQUIRED')
    row=next((r for r in catalog['regions'] if r['slug']==slug),None)
    require(row is not None,'SELECTED_REGION_NOT_IN_COMPLETE_SUPPLY')
    sys.path.insert(0,str(Path(root)/'tests'))
    from native_r2_acceptance import probe_payload
    probe_payload(mirror.read(probe_ref,8192),row['feature'],slug)
    top={(ref['key'],ref['sha256']) for ref in (catalog_ref,source_ref,probe_ref)}
    refs=[ref for identity,ref in sorted(mirror.refs.items()) if identity not in top]
    return validate(dict(schema=SCHEMA,source_sha=source_sha,slug=slug,catalog=catalog_ref,
        catalog_request=source_ref,probe=probe_ref,objects=refs,budgets=budgets),source_sha)


def read_ref(path):
    with Path(path).open('rb') as stream:raw=stream.read(8193)
    require(0 < len(raw) <= 8192,'DESCRIPTOR_FILE_BOUNDS')
    return decode(raw)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('source-sha','catalog-ref','catalog-request-ref','probe-ref','objects','slug','output'):
        parser.add_argument('--'+name,required=True)
    for name in ('metadata-bytes','source-download-bytes','archive-bytes','output-bytes'):
        parser.add_argument('--'+name,type=int,required=True)
    args=parser.parse_args();source_revision(args.source_sha)
    budgets={name:getattr(args,name) for name in
             ('metadata_bytes','source_download_bytes','archive_bytes','output_bytes')}
    request=plan(args.source_sha,read_ref(args.catalog_ref),read_ref(args.catalog_request_ref),
                 read_ref(args.probe_ref),LocalObjects(args.objects),args.slug,budgets)
    raw=canonical_bytes(request);write_private(args.output,raw)
    print(json.dumps(dict(event='PRIVATE_ISOLATED_REQUEST_PLANNED',sha256=hashlib.sha256(raw).hexdigest(),
                          bytes=len(raw),objects=len(request['objects']),published=False)))


if __name__=='__main__':
    try:main()
    except Exception as error:
        print(json.dumps(dict(event='ISOLATED_REQUEST_REJECTED',error=type(error).__name__)),file=sys.stderr)
        raise SystemExit(1) from None
