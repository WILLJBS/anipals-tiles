"""Full roster execution using the unchanged reviewed collector and private checkpoints."""
import argparse
from contextlib import redirect_stdout, redirect_stderr
import json
import signal
import sys
import time
from cloud_collect_places import Deadline, unpack, bundled_uploader
from cloud_collector_io import Journal
from cloud_collector_net import cloud_range_class, wrap_collect
from cloud_full_contract import namespace_spec, assert_lineage_current
from cloud_full_seed import install_seed


def load_code(store, spec, work):
    work.mkdir(parents=True, exist_ok=True)
    bundle = work/'code.zip'; store.fetch(spec['code'], bundle)
    code = work/'code'; unpack(bundle, code, spec['appSourceSha'])
    sys.path.insert(0, str(code/'tools/discovery-places')); store.uploader = bundled_uploader(code)
    return code


def merge_caches(store, journal, contract):
    for ledger in contract['namespaceLedgers']:
        spec = namespace_spec(store, ledger['namespace'])
        old = Journal(store, ledger['namespace'], spec['sourceBudgetBytes'], spec['priorSourceBytes'])
        for key, value in old.ranges.items():
            if key in journal.ranges and journal.ranges[key] != value: raise ValueError('RANGE_CACHE_CONFLICT')
            journal.ranges[key] = value
        if spec['mode'] == 'full':
            for key, value in old.files.items():
                if key in journal.files and journal.files[key] != value: raise ValueError('FILE_CACHE_CONFLICT')
                journal.files[key] = value
    journal.snapshot()


def collect_full(store, spec, spec_sha, contract, seed, work, execution):
    import duckdb
    if 'expand_cities' in sys.modules: raise ValueError('COLLECTOR_REQUIRES_FRESH_PROCESS')
    load_code(store, spec, work)
    roster, index = work/'roster.json', work/'index.parquet'
    store.fetch(spec['roster'], roster); store.fetch(spec['index'], index)
    from city_scope import city_source_rows, city_regions
    import expand_cities as engine
    rows = city_source_rows(json.loads(roster.read_text())); regions = city_regions(rows)
    if len(rows) != 6222 or len(regions) != 6222: raise ValueError('GLOBAL_ROSTER_COUNT_DIFFERS')
    assert_lineage_current(store, seed, contract, spec_sha)
    journal = Journal(store, spec_sha, spec['sourceBudgetBytes'], spec['priorSourceBytes'])
    con = duckdb.connect()
    try: install_seed(journal, seed, regions, index, con, engine.REGISTRY, persist=False)
    finally: con.close()
    merge_caches(store, journal, contract)
    engine.RangeCache = cloud_range_class(engine.RangeCache, journal, time.monotonic()+spec['maxMinutes']*60)
    engine.collect_file = wrap_collect(engine.collect_file, journal)
    output = work/'output'; output.mkdir(exist_ok=True)
    args = argparse.Namespace(output=output, cities=roster, index=index, limit=None, proxy=None,
                              budget=spec['sourceBudgetBytes'], reuse_cache=[], workers=1, themes=['base', 'places'])
    complete, summary, failure = False, None, None
    def expire(*unused): raise Deadline()
    old = signal.signal(signal.SIGALRM, expire); signal.alarm(spec['maxMinutes']*60)
    try:
        with (work/'collector.log').open('w') as log, redirect_stdout(log), redirect_stderr(log):
            summary = engine.run(args)
        complete = (not summary['failures'] and not summary.get('geometryLookupFailures')
                    and summary['filesCompleted'] == summary['filesTotal'] == seed['sourceFilesTotal']
                    and set(summary.get('cityComplete', {})) == set(regions)
                    and all(summary['cityComplete'].values()))
    except BaseException as error: failure = type(error).__name__
    finally:
        signal.alarm(0); signal.signal(signal.SIGALRM, old)
        outputs = {name: store.put(output/name) for name in
                   ('named-candidates.json', 'dedup-report.json', 'summary.json', 'progress.json') if (output/name).is_file()}
        log_ref = store.put(work/'collector.log') if (work/'collector.log').is_file() else None
        state = journal.snapshot()
        result = {'schema': 'anipals-cloud-collector-result-v1', 'complete': complete, 'specSha256': spec_sha,
                  'execution': execution, 'appSourceSha': spec['appSourceSha'], 'roster': spec['roster'],
                  'index': spec['index'], 'sourceScopeCount': len(rows), 'sourceBytesCharged': journal.used,
                  'budgetBytes': journal.budget, 'priorSourceBytes': spec['priorSourceBytes'],
                  'budgetLedger': {'namespace': spec_sha, 'checkpointSequence': journal.seq,
                      'unsettledReservedBytes': sum(journal.pending.values()), 'scope': 'global-lineage',
                      'lineageSha256': contract['lineageSha256']},
                  'candidateCount': summary.get('uniqueNamedCandidates') if summary else None,
                  'filesCompleted': summary.get('filesCompleted') if summary else None,
                  'failureType': failure, 'outputs': outputs, 'log': log_ref, 'checkpoint': state,
                  'publication': 'pending', 'navigationTargetsPublished': 0}
        receipt = store.save_json(journal.prefix+'results/', result)
    return {'complete': complete, 'sourceScopes': len(rows), 'sourceBytesCharged': journal.used,
            'unsettledReservedBytes': sum(journal.pending.values()),
            'candidateCount': result['candidateCount'], 'privateResult': receipt}
