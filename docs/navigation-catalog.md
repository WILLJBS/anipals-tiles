# Reviewed navigation supply catalog

`tools/plan_navigation_catalog.py` is an offline, read-only supply planner. It
writes one new private local catalog. It cannot upload, activate a graph, change
a pointer, delete local graphs, dispatch CI or assert that cities route. The
catalog has schema `anipals-navigation-catalog-v1`, deliberately different from
runtime `regional_composite` schemas. Runtime storage ownership, retained local
rollback and a reviewed activation-index assembler remain separate prerequisites.

## Trust boundary

The caller reviews and locks the entire request with its exact byte SHA256. That
request explicitly pins the registered-source file, complete source-scope map,
bucket identity, source metadata, original runner allowlists, and every receipt
and graph manifest. A local mirror contains complete object bytes using their
SHA256 as filenames. Source metadata uses immutable `archive/sha256` keys;
receipts and graph manifests must use their existing exact navigation keys.

This planner verifies those exact bytes; it does not authenticate an arbitrary
self-authored receipt as a historical CI execution. Before locking a real request,
retrieve its descriptors from the actual private R2 receipts and verify the
corresponding successful regional CI job and reviewed runner. A failed overall
migration matrix may contain successful complete regional jobs. Keep those jobs'
original receipts. Do not relabel them with the recovery run's checkout SHA or
claim the entire matrix succeeded. Offline synthetic tests are never actual
upload or native-route evidence.

Every source names one committed `migration-contracts.json` registration and its
exact definition hash. `validate_profile_supply` still validates the whole source
release, READY, parts, feature locks, pinned native image and required build
proofs. The original runner SHA is explicitly allowlisted, while each receipt's
`registered_source` and recomputed `migration_identity` remain its original value.
The runner can differ from the current planner checkout because no receipt is
regenerated. A runner allowlist never overrides a different source definition,
release, part list, graph fingerprint, feature, bucket or ABI.

`tools/migration_receipts.py` is the shared receipt/manifest verifier used by this
planner and `tests/native_r2_acceptance.py`. The latter retains its existing public
helper functions as delegation wrappers. Both now also enforce manifest length
against the receipt, exact manifest SHA identity, structural report tile count,
and equality of propagated source/native proof to the validated READY regional
entry. The normal source snapshot/native validators also check the manifest.
Legacy original supply retains the existing explicitly registered READY exception.

## Exact request shape

All listed fields are required; unknown fields fail. Descriptor sizes are strict
positive integers. The complete request limit is 2 MiB; release/READY/receipt/
manifest limits are 16/4/8/32 MiB respectively. Duplicate JSON keys, nonfinite JSON,
truncated bytes, mismatched hashes, unsafe local objects and substituted keys fail.

```json
{
  "schema": "anipals-navigation-catalog-request-v1",
  "registry_sha256": "<SHA256 of tools/migration-contracts.json>",
  "scope_sha256": "<SHA256 of deploy/global-additions-scopes.json>",
  "bucket": "<private navigation bucket>",
  "sources": [{
    "registry": "original61",
    "definition_sha256": "<canonical hash of this registration definition>",
    "allowed_runner_shas": ["<original reviewed checkout>", "<reviewed recovery checkout>"],
    "release": {"key": "<immutable archive key>", "sha256": "<digest>", "bytes": 1},
    "ready": {"key": "<immutable archive key>", "sha256": "<digest>", "bytes": 1}
  }],
  "deferred_contracts": ["gap3", "additions129"],
  "receipts": [{
    "registry": "original61",
    "slug": "<exact registered slug>",
    "runner_sha": "<this receipt's original reviewed checkout>",
    "receipt": {"key": "<original migration receipt key>", "sha256": "<digest>", "bytes": 1},
    "manifest": {"key": "<immutable graph manifest key>", "sha256": "<digest>", "bytes": 1}
  }]
}
```

The skeleton illustrates field shapes only; it is not executable evidence. A
selected `original61` source needs all 61 receipts; a selected `gap3` needs all
three; `additions129` needs all 129. Missing, extra or duplicate graph identities
fail. Every registered group must be selected or explicitly deferred. Deferring a
group retains its complete slug list and expected scope counts in the output;
it cannot make global supply complete. A 127/129 draft cannot pass the existing
source gate, and deleting two receipt rows from otherwise complete input fails.

The complete plan remains 193 isolated graph identities and 6,222 source rows.
The planner reuses `check_global_build.validate_inputs` to validate the entire
source-row mapping and required probe contracts. Each output region retains its
assigned source-row IDs, even when another source group is deferred. Combining
old61 and gap3 produces 64 catalog graphs / 5,102 assigned rows and explicitly
retains 129 deferred graph identities. These are source scopes, not a distinct
city count and not routes that passed.

## Offline invocation and output

From the reviewed tiles checkout, with private files outside tracked source:

```sh
python3 tools/plan_navigation_catalog.py \
  --request "$PRIVATE_WORK/catalog-request.json" \
  --request-sha "$REVIEWED_REQUEST_SHA256" \
  --objects "$PRIVATE_WORK/objects-by-sha" \
  --output "$PRIVATE_WORK/catalog.json"
```

Output is exclusively created with mode 0600 and is never overwritten. Console
output contains only the new catalog's SHA/byte count, aggregate counts and
explicit non-activation flags. Catalog bytes preserve private source descriptors;
do not publish them as public GitHub artifacts. This command uses no credentials
and performs no network IO. It verifies metadata bytes, not every tile again.

The catalog records `supply_complete`, `tile_bytes_reverified:false`,
`runtime_activated:false`, `scope_routes_verified:false` and
`runtime_ownership_reviewed:false`. Even when all 193 source graph receipts pass,
only `supply_complete` becomes true. An independent runtime index can bind the
exact catalog SHA, but still needs real local rollback identities and its own
storage-ownership validation. A pointer count, locate activation or complete
supply catalog cannot substitute for per-scope legal targets and actual nonzero
routes with endpoint/shape correlation.

## Tests and outstanding activation work

`python3 -m unittest discover -s tests -p 'test_navigation_catalog*.py'` covers
cross-run preservation, exact 64/193 roster accounting, 127/129 failure, unknown
runner, changed definition/source/coverage/ABI/proof, duplicate/missing region,
pilot substitution, object key/SHA/size/truncation, symlinks and private CLI output.
The existing full discovery in deploy/migration CI automatically includes these
new tests. `test_native_r2_acceptance.py` remains the shared diagnostic baseline.
Fixtures explicitly use synthetic object inventories and do not claim R2 uploads.

This change does not resolve runtime same-slug local/R2 ownership or old-local GC
retention. Do not relax Composite's duplicate-slug rejection alone: the existing
local worker could reactivate the old graph and GC could remove the rollback.
Production activation still requires the independent runtime ownership contract,
real isolated R2 cold/hot acceptance, exact deployment identity and actual
source-scope route evidence under the existing API business gates.
