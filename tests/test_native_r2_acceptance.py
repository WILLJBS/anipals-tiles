"""Offline rejection tests only: these never claim native/R2 acceptance."""
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

ROOT = Path(os.environ.get('ANIPALS_TILES_CHECKOUT', Path(__file__).resolve().parents[1]))
sys.path[:0] = [str(ROOT/'tools'), str(ROOT/'deploy')]
from regional_release import canonical_hash
from migration_contract import migration_identity
from native_r2_acceptance import Counter, probe_payload, route_pair, verify_manifest, verify_receipt


class GateTests(unittest.TestCase):
    def setUp(self):
        self.args = SimpleNamespace(contract='fixture', migration_sha='a'*40, slug='test',
                                    tag='tiles-test', manifest_sha='b'*64)
        feature = dict(type='Feature', properties=dict(slug='test'), geometry=dict(type='Polygon',
                       coordinates=[[[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]]]))
        self.profile = dict(name='fixture', definition={'fixture': True}, image='valhalla/valhalla@sha256:'+'c'*64,
                            coverage=dict(type='FeatureCollection', features=[feature]))
        self.plans = [dict(slug='test', fingerprint='d'*64, parts=[dict(name='test.tar', size=4, sha256='e'*64)])]
        self.release = dict(tag_name='tiles-test')
        registered = dict(schema=1, registry='fixture', checkout_sha='a'*40,
                          definition_sha256=canonical_hash(self.profile['definition']))
        contract = migration_identity(self.release, self.profile['image'], self.profile['coverage'], self.plans, registered)
        self.receipt = dict(schema=1, bucket='fixture-bucket', slug='test', contract=contract,
                            registered_source=registered, graph_fingerprint='f'*64,
                            manifest_sha256='b'*64, manifest_size=100, tiles=1, feature=feature)

    def check(self, receipt):
        return verify_receipt(receipt, self.args, self.profile, self.plans, self.release, 'fixture-bucket')

    def test_registered_complete_receipt(self):
        self.assertEqual(self.check(self.receipt), self.receipt['feature'])

    def test_pilot_and_substituted_contracts_rejected(self):
        for field, value in [('tiles', 20.0), ('bucket', 'another'), ('slug', 'other'),
                             ('manifest_sha256', 'c'*64), ('contract', '0'*64),
                             ('registered_source', dict(checkout_sha='b'*40)), ('tiles', 0)]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.check(dict(self.receipt, **{field: value}))
        with self.assertRaises(ValueError):
            self.check(dict(schema=1, verified_tiles=20, manifest_published=False))

    def test_coverage_substitution_rejected(self):
        value = copy.deepcopy(self.receipt)
        value['feature']['geometry']['coordinates'][0][0][0] = -1
        with self.assertRaises(ValueError):
            self.check(value)

    def test_manifest_checks_abi_graph_source_and_bytes(self):
        inventory = {'2/000/001.gph': dict(size=3, sha256='e'*64)}
        graph = canonical_hash({p: row['sha256'] for p, row in inventory.items()})
        self.receipt['graph_fingerprint'] = graph
        manifest = dict(schema=1, slug='test', graph_fingerprint=graph, image=self.profile['image'],
                        validation='gph-v3-index-v1', coverage_sha256=canonical_hash(self.receipt['feature']),
                        tiles=inventory, validation_report=dict(tiles=1),
                        source=dict(kind='github-release-region', tag='tiles-test', parts=self.plans[0]['parts']))
        def check(value, bad_hash=False):
            raw = json.dumps(value).encode()
            self.args.manifest_sha = '0'*64 if bad_hash else hashlib.sha256(raw).hexdigest()
            self.receipt.update(manifest_size=len(raw), manifest_sha256=self.args.manifest_sha)
            return verify_manifest(raw, self.args, self.receipt, self.receipt['feature'], self.profile, self.plans[0])
        check(manifest)
        for key, value in [('image', 'valhalla/valhalla@sha256:'+'0'*64), ('graph_fingerprint', '0'*64),
                           ('coverage_sha256', '0'*64), ('source', dict(kind='other')), ('validation_report', {})]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                check(dict(manifest, **{key: value}))
        with self.assertRaises(ValueError):
            check(manifest, True)

    def test_source_proof_numeric_boolean_substitution_rejected(self):
        from migration_receipts import verify_manifest
        inventory = {'2/000/001.gph': dict(size=3, sha256='e'*64)}
        graph = canonical_hash({p: row['sha256'] for p, row in inventory.items()})
        manifest = dict(schema=1, slug='test', graph_fingerprint=graph, image=self.profile['image'],
                        validation='gph-v3-index-v1', coverage_sha256=canonical_hash(self.receipt['feature']),
                        tiles=inventory, validation_report=dict(tiles=1), input_provenance={'counter': True},
                        source=dict(kind='github-release-region', tag='tiles-test', parts=self.plans[0]['parts']))
        raw = json.dumps(manifest).encode(); self.args.manifest_sha=hashlib.sha256(raw).hexdigest()
        self.receipt.update(graph_fingerprint=graph, manifest_size=len(raw), manifest_sha256=self.args.manifest_sha)
        ready = dict(graph_fingerprint=graph, tiles=1, input_provenance={'counter': 1})
        with self.assertRaisesRegex(ValueError, 'READY_PROOF'):
            verify_manifest(raw, self.args, self.receipt, self.receipt['feature'], self.profile, self.plans[0], ready)

    def test_probe_rejects_snap_radius_and_outside(self):
        value = dict(schema=1, slug='test', provenance='Synthetic fixture, not production acceptance',
                     payload=dict(costing='pedestrian', locations=[dict(lat=1, lon=1), dict(lat=1.001, lon=1.001)]))
        probe_payload(json.dumps(value).encode(), self.receipt['feature'], 'test')
        for field, change in [('radius', 10000), ('lat', 10)]:
            altered = copy.deepcopy(value); altered['payload']['locations'][0][field] = change
            with self.assertRaises(ValueError):
                probe_payload(json.dumps(altered).encode(), self.receipt['feature'], 'test')

    def test_counter_rejects_another_graph_before_source(self):
        counter = Counter()
        def never(_):
            self.fail('source called for invalid graph')
        fetch = counter.tile_reader(never, 'navigation/graphs/test/locked/tiles/', {'2/000.gph': {}})
        with self.assertRaises(ValueError):
            list(fetch('navigation/graphs/other/locked/tiles/2/000.gph'))

    def pair(self, cold_io=True, hot_io=False, hot_shape=False, bridge_failures=0):
        counter = Counter()
        class FakeEngine:
            calls = 0
            def request(self, *_):
                self.calls += 1
                if (self.calls == 1 and cold_io) or (self.calls == 2 and hot_io):
                    counter.before_send()
                    list(counter.tile_reader(lambda _: [b'verified'], 'test/', {'tile': {}})('test/tile'))
                return dict(trip=dict(units='kilometers', summary=dict(length=0.25),
                                      legs=[dict(shape='changed' if self.calls == 2 and hot_shape else 'shape')]))
        return route_pair(FakeEngine(), {}, {}, counter, SimpleNamespace(failures=bridge_failures))

    def test_cold_get_hot_zero_counted(self):
        records = self.pair()
        self.assertEqual((records[0]['http_gets'], records[1]['http_gets']), (1, 0))

    def test_hot_network_cold_without_network_and_changed_route_rejected(self):
        for args in [dict(cold_io=False), dict(hot_io=True), dict(hot_shape=True), dict(bridge_failures=1)]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.pair(**args)


if __name__ == '__main__':
    unittest.main()
