import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'deploy'))
from regional_release import validate_supply, canonical_hash, LEGACY_TAG

IMAGE='valhalla/valhalla@sha256:'+'a'*64


def fixture():
    roster=dict(region=[dict(slug='a',region='a')])
    feature=dict(type='Feature',properties=dict(slug='a',region='a',pbf_url='https://download.geofabrik.de/a-latest.osm.pbf'),
                 geometry=dict(type='Polygon',coordinates=[[[0,0],[1,0],[1,1],[0,0]]]))
    coverage=dict(type='FeatureCollection',features=[feature])
    part=dict(name='tiles-a.tar-00',size=12,sha256='b'*64)
    release=dict(tag_name='tiles-new',draft=False,prerelease=False,assets=[dict(name=part['name'],size=12,
        digest='sha256:'+part['sha256'],browser_download_url='https://example.com/part')])
    ready=dict(schema=2,production=True,graph_layout='isolated-regions-v1',image=IMAGE,
        coverage_sha256=canonical_hash(coverage),regions=['a'],parts=[part],region_manifests=dict(a=dict(
            graph_fingerprint='c'*64,validation='gph-v3-index-v1',tiles=1,coverage_sha256=canonical_hash(feature),parts=[part])))
    return release,ready,roster,coverage


def marker(release, payload, digest=True):
    blob=payload if isinstance(payload,bytes) else json.dumps(payload).encode()
    release['assets']=[a for a in release['assets'] if a['name']!='READY']
    asset=dict(name='READY',size=len(blob))
    if digest:asset['digest']='sha256:'+hashlib.sha256(blob).hexdigest()
    release['assets'].append(asset)
    return blob


class RegionalReleaseTests(unittest.TestCase):
    def test_valid_schema2_supply(self):
        release,ready,roster,coverage=fixture()
        self.assertIsNone(validate_supply(release,marker(release,ready),roster,IMAGE,coverage))

    def test_only_exact_legacy_tag_and_marker_may_bypass_schema2(self):
        release,_,roster,coverage=fixture()
        release['tag_name']=LEGACY_TAG
        self.assertIsNone(validate_supply(release,marker(release,b'ok\n'),roster,IMAGE,coverage))
        self.assertIsNone(validate_supply(release,marker(release,b'ok\n',False),roster,IMAGE,coverage))
        for tag,body in [(LEGACY_TAG,b'bad'),('tiles-other',b'ok\n')]:
            release['tag_name']=tag
            with self.assertRaises(ValueError):validate_supply(release,marker(release,body),roster,IMAGE,coverage)
        release['tag_name']=LEGACY_TAG
        blob=marker(release,b'ok\n');release['assets'][0]['digest']='broken'
        with self.assertRaises(ValueError):validate_supply(release,blob,roster,IMAGE,coverage)

    def test_ready_bytes_size_digest_and_schema_cannot_be_substituted(self):
        for fault in ('size','digest','missing-digest','schema','production','layout','image','coverage','regions','fingerprint','tiles','validator','regional-coverage','part','extra-part'):
            release,ready,roster,coverage=fixture()
            region=ready['region_manifests']['a']
            if fault=='schema':ready['schema']=1
            if fault=='production':ready['production']=False
            if fault=='layout':ready['graph_layout']='overlay'
            if fault=='image':ready['image']='untrusted'
            if fault=='coverage':ready['coverage_sha256']='0'*64
            if fault=='regions':ready['regions']=[]
            if fault=='fingerprint':region['graph_fingerprint']='bad'
            if fault=='tiles':region['tiles']=0
            if fault=='validator':region['validation']='unchecked'
            if fault=='regional-coverage':region['coverage_sha256']='0'*64
            if fault=='part':region['parts'][0]['size']=13
            if fault=='extra-part':ready['parts'].append(dict(name='extra',size=1,sha256='f'*64))
            blob=marker(release,ready)
            if fault=='size':release['assets'][-1]['size']+=1
            if fault=='digest':release['assets'][-1]['digest']='sha256:'+'0'*64
            if fault=='missing-digest':del release['assets'][-1]['digest']
            with self.subTest(fault=fault),self.assertRaises(ValueError):validate_supply(release,blob,roster,IMAGE,coverage)

    def test_coverage_pbf_and_duplicate_assets_rejected_even_with_updated_hashes(self):
        release,ready,roster,coverage=fixture()
        coverage['features'][0]['properties']['pbf_url']='https://example.com/not-the-extract'
        ready['coverage_sha256']=canonical_hash(coverage)
        ready['region_manifests']['a']['coverage_sha256']=canonical_hash(coverage['features'][0])
        with self.assertRaises(ValueError):validate_supply(release,marker(release,ready),roster,IMAGE,coverage)
        release,ready,roster,coverage=fixture();blob=marker(release,ready)
        release['assets'].append(copy.deepcopy(release['assets'][0]))
        with self.assertRaises(ValueError):validate_supply(release,blob,roster,IMAGE,coverage)


if __name__=='__main__':unittest.main()
