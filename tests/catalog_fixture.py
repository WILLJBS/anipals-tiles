"""Synthetic immutable receipt mirrors; never a real upload or route proof."""
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'tools'), str(ROOT/'deploy')]
from migration_contract import load_profile, migration_identity, validate_profile_supply
from navigation_catalog_inputs import LocalObjects
from regional_release import canonical_hash
from release_manifest import ready as make_ready


def raw(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


class Fixture:
    def __init__(self, directory, groups=('original61', 'gap3')):
        self.root = Path(directory)
        self.objects = LocalObjects(self.root)
        self.sources, self.receipts = [], []
        self.group_data = {}
        for name in groups:
            self.group(name)
        registry = (ROOT/'tools/migration-contracts.json').read_bytes()
        scopes = (ROOT/'deploy/global-additions-scopes.json').read_bytes()
        self.request = dict(schema='anipals-navigation-catalog-request-v1',
            registry_sha256=hashlib.sha256(registry).hexdigest(), scope_sha256=hashlib.sha256(scopes).hexdigest(),
            bucket='fixture-private-bucket', sources=self.sources,
            deferred_contracts=[n for n in json.loads(registry)['contracts'] if n not in groups], receipts=self.receipts)

    def put(self, value, key=None):
        data = value if isinstance(value, bytes) else raw(value)
        digest = hashlib.sha256(data).hexdigest()
        (self.root/digest).write_bytes(data)
        return dict(key=key(digest) if key else 'archive/sha256/%s/%s' % (digest[:2], digest),
                    sha256=digest, bytes=len(data))

    def group(self, name):
        p = load_profile(name); image = p['image']; tag = 'tiles-fixture-'+name
        release = dict(tag_name=tag, draft=False, prerelease=False, assets=[])
        regional, objects = [], {}
        actual_gap = json.loads((ROOT/'tests/fixtures/gap-release-20261003.json').read_text())
        gap_ready = json.loads(actual_gap['ready_utf8'])['region_manifests']
        for feature in p['coverage']['features']:
            slug = feature['properties']['slug']; props = feature['properties']
            part = dict(name='tiles-'+slug+'.tar-00', size=4, sha256='e'*64)
            release['assets'].append(dict(name=part['name'], size=4, digest='sha256:'+part['sha256'],
                browser_download_url='https://github.com/WILLJBS/anipals-tiles/releases/download/'+tag+'/'+part['name']))
            inventory = {'2/000/001.gph': dict(size=3, sha256='d'*64)}
            graph = canonical_hash({k:v['sha256'] for k,v in inventory.items()})
            build = dict(slug=slug, image=image, coverage_sha256=canonical_hash(feature), pbf_url=props['pbf_url'],
                parts=[part], validation=dict(validator='gph-v3-index-v1', tiles=1,
                                            tile_hashes={k:v['sha256'] for k,v in inventory.items()}))
            if props.get('native_probe_sha256'):
                build['native_validation'] = dict(slug=slug, native_version='3.3.0', scope_sha256=props['native_probe_sha256'],
                    probes=[dict(source_row=i, verified=True, distance_km=0.1) for i in props['native_source_rows']])
            if props.get('input_source'):
                build['input_provenance'] = gap_ready[slug]['input_provenance']
            manifest = dict(schema=1, slug=slug, image=image, graph_fingerprint=graph,
                coverage_sha256=canonical_hash(feature), validation='gph-v3-index-v1', tiles=inventory,
                validation_report=dict(tiles=1), source=dict(kind='github-release-region', tag=tag, parts=[part]))
            for key in ('native_validation', 'input_provenance'):
                if key in build: manifest[key] = build[key]
            objects[slug] = (feature, manifest); regional.append(build)
        ready = make_ready(release, p['roster'], regional, image, p['coverage'])
        ready_raw = raw(ready)
        release['assets'].append(dict(name='READY', size=len(ready_raw), digest='sha256:'+hashlib.sha256(ready_raw).hexdigest()))
        plans = validate_profile_supply(p, release, ready_raw)
        source = dict(registry=name, definition_sha256=canonical_hash(p['definition']),
                      allowed_runner_shas=['a'*40, 'b'*40], release=self.put(release), ready=self.put(ready_raw))
        self.sources.append(source)
        self.group_data[name] = dict(profile=p, release=release, ready=ready, objects=objects, plans=plans)
        for index, (slug, (feature, manifest)) in enumerate(objects.items()):
            runner = ('a' if index % 2 else 'b') * 40
            registered = dict(schema=1, registry=name, checkout_sha=runner, definition_sha256=source['definition_sha256'])
            ref = self.put(manifest, lambda h: 'navigation/graphs/%s/%s/manifests/%s.json' % (slug, manifest['graph_fingerprint'], h))
            receipt = dict(schema=1, contract=migration_identity(release,image,p['coverage'],plans,registered),
                registered_source=registered, bucket='fixture-private-bucket', slug=slug, feature=feature,
                graph_fingerprint=manifest['graph_fingerprint'], tiles=1, manifest_sha256=ref['sha256'], manifest_size=ref['bytes'])
            receipt_ref = self.put(receipt, lambda h: 'navigation/migration-receipts/%s/%s/receipt-%s/%s.json' % (runner, tag, slug, h))
            self.receipts.append(dict(registry=name, slug=slug, runner_sha=runner, receipt=receipt_ref, manifest=ref))

    def replace(self, ref, value):
        prefix = ref['key'].rsplit('/', 1)[0]+'/'
        suffix = '.json' if ref['key'].endswith('.json') else ''
        return self.put(value, lambda h: prefix+h+suffix)
