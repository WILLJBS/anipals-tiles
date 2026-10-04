#!/usr/bin/env python3
"""Produce a private offline supply catalog from a reviewed locked request.

No network, credentials, upload or runtime changes. The output is deliberately
not a regional_composite activation index. Local objects use their SHA as filename.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from navigation_catalog import plan
from navigation_catalog_inputs import LocalObjects, checked_bytes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', type=Path, required=True)
    parser.add_argument('--request-sha', required=True)
    parser.add_argument('--objects', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    with args.request.open('rb') as stream:
        raw = stream.read(2*1024*1024+1)
    checked_bytes(raw, args.request_sha, len(raw), 2*1024*1024)
    catalog = plan(raw, args.request_sha, LocalObjects(args.objects))
    output = json.dumps(catalog, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    # Exclusive creation prevents replacing another review's candidate. Private by default.
    fd = os.open(args.output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(output); stream.flush(); os.fsync(stream.fileno())
    print(json.dumps(dict(event='PRIVATE_NAVIGATION_CATALOG_PLANNED', sha256=hashlib.sha256(output).hexdigest(),
                          bytes=len(output), counts=catalog['counts'], supply_complete=catalog['supply_complete'],
                          runtime_activated=False, scope_routes_verified=False)))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps(dict(event='NAVIGATION_CATALOG_REJECTED', error=type(error).__name__)), file=sys.stderr)
        raise SystemExit(1) from None
