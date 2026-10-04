#!/usr/bin/env python3
"""Offline runtime inventory/index artifacts; no upload, pointer or native calls."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from migration_contract import load_profile
from navigation_catalog_inputs import LocalObjects, decode
from runtime_index_assembler import assemble, canonical_bytes
from runtime_local_inventory import capture


def write_private(path, raw):
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    inventory = sub.add_parser('inventory')
    inventory.add_argument('--volume-root', type=Path, required=True)
    inventory.add_argument('--regions-json', required=True)
    inventory.add_argument('--target-identity-sha', required=True)
    inventory.add_argument('--output', type=Path, required=True)
    planner = sub.add_parser('assemble')
    planner.add_argument('--request', type=Path, required=True)
    planner.add_argument('--request-sha', required=True)
    planner.add_argument('--objects', type=Path, required=True)
    planner.add_argument('--volume-root', type=Path, required=True)
    planner.add_argument('--output-directory', type=Path, required=True)
    args = parser.parse_args()
    if args.action == 'inventory':
        result = capture(args.volume_root, decode(args.regions_json), load_profile('original61')['image'], args.target_identity_sha)
        raw = canonical_bytes(result); write_private(args.output, raw)
        print(json.dumps(dict(event='PRIVATE_TARGET_INVENTORY_OBSERVED', sha256=hashlib.sha256(raw).hexdigest(),
                              bytes=len(raw), local_regions=len(result['regions']), native_rollback_verified=False)))
        return
    with args.request.open('rb') as stream:
        request = stream.read(2*1024*1024+1)
    result = assemble(request, args.request_sha, LocalObjects(args.objects), args.volume_root)
    args.output_directory.mkdir(mode=0o700)  # Exclusive bundle; never overwrite another review.
    for name in ('index','coverage'):
        write_private(args.output_directory/(name+'.json'), canonical_bytes(result[name]))
    audit = {k: v for k, v in result.items() if k not in ('index','coverage')}
    audit['coverage_sha256'] = hashlib.sha256(canonical_bytes(result['coverage'])).hexdigest()
    write_private(args.output_directory/'review.json',canonical_bytes(audit))
    print(json.dumps(dict(event='PRIVATE_RUNTIME_INDEX_ASSEMBLED', sha256=result['index_sha256'],
                         selected_regions=len(result['selected_regions']), production_supply_complete=result['production_supply_complete'],
                         published=False, runtime_activated=False)))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps(dict(event='RUNTIME_INDEX_REJECTED', error=type(error).__name__)),file=sys.stderr)
        raise SystemExit(1) from None
