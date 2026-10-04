#!/usr/bin/env python3
"""Explicit native-verified rollback to the local graph retained by a pinned index."""
import argparse
import json
from pathlib import Path
from regional_download import verify_native
from regional_ownership import checked_marker, read_control, rollback
from regional_gc import identity


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root', default='/data')
    parser.add_argument('--slug', required=True)
    parser.add_argument('--index-sha256', required=True)
    parser.add_argument('--local-fingerprint', required=True)
    args = parser.parse_args()
    identity(args.slug, args.local_fingerprint)
    parent = Path(args.data_root).resolve()/'regions'/args.slug
    control = read_control(parent)
    expected = dict(storage='local', fingerprint=args.local_fingerprint)
    if not control or control['index_sha256'] != args.index_sha256 or control['rollback'] != expected:
        raise ValueError('rollback request differs from reviewed retained graph')
    descriptor = checked_marker(parent, expected)
    result = verify_native(descriptor)
    rollback(args.data_root, args.slug, args.index_sha256, args.local_fingerprint, result)
    print(json.dumps(dict(event='explicit_local_rollback', slug=args.slug, fingerprint=args.local_fingerprint)))


if __name__ == '__main__':
    main()
