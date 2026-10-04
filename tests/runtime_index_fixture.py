"""Synthetic mounted volumes and source catalogs; never production rollback proof."""
import hashlib
import json
from pathlib import Path
from catalog_fixture import Fixture, raw, ROOT
from navigation_catalog import plan
from regional_release import canonical_hash
from runtime_local_inventory import capture

BODY=b'abc'


class RuntimeFixture:
    def __init__(self, folder, complete=False, selected=None):
        self.root=Path(folder).resolve();self.mirror=self.root/'objects';self.mirror.mkdir()
        self.volume=self.root/'volume';self.volume.mkdir()
        self.fixture=Fixture(self.mirror,('original61','gap3','additions129') if complete else ('original61','gap3'))
        self.rewrite_local_content()
        source=raw(self.fixture.request)
        self.source=self.fixture.put(source)
        self.catalog=plan(source,hashlib.sha256(source).hexdigest(),self.fixture.objects)
        self.catalog_ref=self.fixture.put(self.catalog)
        selected=selected or (sorted(r['slug'] for r in self.catalog['regions']) if complete else ['europe-germany'])
        self.image=self.catalog['image'];self.local_plans={p['slug']:p for p in self.fixture.group_data['original61']['plans']}
        local=sorted(set(selected)&set(self.local_plans))
        for slug in local:self.seed(slug)
        observed=capture(self.volume,selected,self.image,'1'*64)
        probes={}
        for row in self.catalog['regions']:
            if row['slug'] not in selected:continue
            geom=row['feature']['geometry'];ring=geom['coordinates'][0] if geom['type']=='Polygon' else geom['coordinates'][0][0]
            probes[row['slug']]=[dict(lat=ring[0][1],lng=ring[0][0])]
        self.request=dict(schema='anipals-runtime-index-request-v1',catalog=self.catalog_ref,catalog_request=self.source,
            mode='production-candidate' if complete else 'isolated-acceptance',selected_regions=selected,probes=probes,
            target_inventory=self.fixture.put(observed),target_identity_sha256='1'*64,previous_index_sha256=None)

    def rewrite_local_content(self):
        group=self.fixture.group_data['original61'];ready=group['ready'];release=group['release']
        for entry in self.fixture.receipts:
            if entry['registry']!='original61':continue
            slug=entry['slug'];manifest=json.loads((self.mirror/entry['manifest']['sha256']).read_bytes())
            manifest['tiles']={'2/000/001.gph':dict(size=len(BODY),sha256=hashlib.sha256(BODY).hexdigest())}
            fp=canonical_hash({k:v['sha256'] for k,v in manifest['tiles'].items()});manifest['graph_fingerprint']=fp
            entry['manifest']=self.fixture.put(manifest,lambda digest:'navigation/graphs/%s/%s/manifests/%s.json'%(slug,fp,digest))
            receipt=json.loads((self.mirror/entry['receipt']['sha256']).read_bytes())
            receipt.update(graph_fingerprint=fp,manifest_sha256=entry['manifest']['sha256'],manifest_size=entry['manifest']['bytes'])
            entry['receipt']=self.fixture.replace(entry['receipt'],receipt)
            ready['region_manifests'][slug]['graph_fingerprint']=fp
        ready_raw=raw(ready);asset=next(a for a in release['assets'] if a['name']=='READY')
        asset.update(size=len(ready_raw),digest='sha256:'+hashlib.sha256(ready_raw).hexdigest())
        source=next(s for s in self.fixture.sources if s['registry']=='original61')
        source['ready']=self.fixture.put(ready_raw);source['release']=self.fixture.put(release)

    def seed(self,slug):
        plan=self.local_plans[slug];fp=plan['fingerprint'];parent=self.volume/'regions'/slug
        folder=parent/fp;tiles=folder/'tiles/2';tiles.mkdir(parents=True)
        (tiles/'000').mkdir();(tiles/'000/001.gph').write_bytes(BODY)
        marker=dict(slug=slug,fingerprint=fp,release=plan['release'],tile_count=1,tile_dir='regions/%s/%s/tiles'%(slug,fp))
        (folder/'.complete.json').write_bytes(raw(marker))
        (folder/'.identity.json').write_bytes(raw(dict(fingerprint=fp,image=self.image,bytes=len(BODY))))
        (parent/'active.json').write_bytes(raw(dict(fingerprint=fp,release=plan['release'])))
        (parent/'.activation.lock').touch()
