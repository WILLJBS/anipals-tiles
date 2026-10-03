"""Reviewed migration inputs; separate from runtime graph configuration."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'deploy'))
from regional_release import canonical_hash, validate_supply
from regional_download import build_plans


def source_revision(value, root=ROOT):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{40}', value):
        raise ValueError('exact reviewed source SHA required')
    actual = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
    if actual != value:
        raise ValueError('checkout differs from reviewed source SHA')


def load_profile(name='original61', root=ROOT):
    registry = json.loads((root/'tools/migration-contracts.json').read_text())
    if registry.get('schema') != 1 or name not in registry.get('contracts', {}):
        raise ValueError('unknown registered migration contract')
    definition = registry['contracts'][name]
    if definition.get('native_version') != '3.3.0' or set(definition.get('files', {})) != {'roster', 'coverage', 'image'}:
        raise ValueError('unsupported migration ABI or input contract')
    decoded = {}
    for kind, lock in definition['files'].items():
        path = root/lock['path']
        if not path.resolve().is_relative_to(root.resolve()):
            raise ValueError('migration input escapes reviewed checkout')
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != lock['sha256']:
            raise ValueError('registered migration input SHA mismatch: ' + kind)
        decoded[kind] = raw.decode().strip() if kind == 'image' else json.loads(raw)
    if not re.fullmatch(r'valhalla/valhalla@sha256:[a-f0-9]{64}', decoded['image']):
        raise ValueError('migration native image must be immutable')
    rows = decoded['roster']['region']; features = decoded['coverage']['features']
    slugs = {row['slug'] for row in rows}
    if (type(definition['regions']) is not int or len(rows) != definition['regions']
            or len(slugs) != len(rows) or len(features) != len(rows)
            or {f['properties']['slug'] for f in features} != slugs):
        raise ValueError('registered migration roster/coverage differs')
    return dict(name=name, definition=definition, **decoded)


def source_contract(profile, source_sha):
    source_revision(source_sha)
    return dict(schema=1, registry=profile['name'], checkout_sha=source_sha,
                definition_sha256=canonical_hash(profile['definition']))


def validate_profile_supply(profile, release, ready):
    validate_supply(release, ready, profile['roster'], profile['image'], profile['coverage'])
    plans = build_plans(release, profile['roster'], profile['image'])
    # Source metadata cannot move a trusted part to another repository/release URL.
    base = 'https://github.com/WILLJBS/anipals-tiles/releases/download/' + release['tag_name'] + '/'
    for plan in plans:
        for part in plan['parts']:
            if part['url'] != base + part['name']:
                raise ValueError('part URL differs from exact source release')
    return plans


def migration_identity(release, image, coverage, plans, registered_source=None):
    identity = dict(release=release['tag_name'], image=image,
        coverage_sha256=canonical_hash(coverage), regions={p['slug']: p['fingerprint'] for p in plans})
    if registered_source is not None:
        identity['registered_source'] = registered_source
    return canonical_hash(identity)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', required=True)
    parser.parse_args()
    names = json.loads((ROOT/'tools/migration-contracts.json').read_text())['contracts']
    profiles = [load_profile(name) for name in names]
    slugs = [row['slug'] for profile in profiles for row in profile['roster']['region']]
    if len(slugs) != len(set(slugs)):
        raise ValueError('registered migration graph identities overlap')
    print(json.dumps({p['name']: p['definition']['regions'] for p in profiles}))
