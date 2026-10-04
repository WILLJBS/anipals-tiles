# Offline runtime-index assembly from reviewed supply and a real target volume

`tools/plan_runtime_index.py` closes the catalog-to-runtime format boundary. It
only reads local files and writes new private review artifacts. It does not
connect to cloud services, publish an index, change ownership or active pointers,
dispatch a job, run native routing, or activate production.

The input chain is deliberately stronger than copying catalog rows into a JSON
index. The reviewed assembly request pins the exact supply catalog bytes and its
original reviewed catalog request. The assembler reruns `navigation_catalog.plan`
against the complete local object mirror and requires the supplied catalog to
match that recomputed result. A hand-edited `supply_complete` field, missing
source, substituted feature/image/graph, changed receipt or incomplete registered
source cannot survive this check. The original complete 129-source validator is
unchanged; a 127/129 draft remains invalid.

## Production versus isolated acceptance

`mode=production-candidate` requires all 193 registered graph identities, no
deferred source groups, complete 6,222 source-row accounting, and selection of
exactly those 193 graphs. It selects R2 storage for every graph and requires all
61 original local rollback generations on the target volume. The resulting
baseline coverage remains the exact registered original61 coverage.

`mode=isolated-acceptance` requires an explicit nonempty subset of the verified
catalog. Complete source groups are still validated before filtering. Its
coverage companion includes only selected baseline features, while selected
additional regions remain remote rows. This companion must be used in the
isolated runtime. Both index review metadata and the review artifact state
`production_supply_complete:false`, even if the underlying catalog happens to
contain all 193 graphs. A 26-region index or a one-region native acceptance is
never a global production-completion claim.

Both modes still report `runtime_activated:false`, `published:false`,
`scope_routes_verified:false` and `native_rollback_verified:false`. Complete
supply, byte validation and a candidate index are not actual route acceptance.

## Observing rollback generations instead of inventing them

The `inventory` subcommand requires the actual target volume mounted at a
canonical, non-symlink path. It reads existing activation locks in shared mode;
it never creates lock files or writes the volume. The selected graph slugs must
be exact registered identities. For each selected original region it observes:

- the active pointer or the explicitly retained local identity from the current
  ownership record;
- exact `.complete.json` and `.identity.json` bytes, hashes and decoded values;
- local storage identity, relative tile path, positive tile count, native image,
  source fingerprint and recorded extraction bytes;
- every tile path, size and full SHA256, read sequentially in 1 MiB chunks.

Actual file count and bytes must match completion/identity metadata. Symlinks,
unsafe tile paths, missing locks/markers/tiles, nonlocal rollback identities,
empty tiles, mismatched image and malformed storage state fail. The inventory
records hashes of the exact tile inventory and graph hashes, without embedding
all tile bytes. It also records selected runtime pointers/ownership states,
target identity SHA, and the mounted root's device/inode.

The target identity SHA is an operator-reviewed binding to independently
established deployment/volume identity. The tool cannot authenticate a cloud
volume label from an arbitrary caller's assertion. Production review must obtain
that identity and the mounted path from the actual deployment, not a synthetic
directory or a release-derived guess. Unit-test volumes are explicitly synthetic
and provide no such production evidence.

Assembly reads the same live target volume again and requires an exact match to
the locked inventory. It then compares each observed local source fingerprint
and release with the validated original source plan, and compares the observed
full tile SHA inventory with that region's verified remote manifest. The source
plan is only an expected-value check: it is never used to fabricate an observed
local generation. Even same-size tile corruption is detected. Existing ownership
index identities must also match the request's explicit previous-index SHA.

This performs two complete local reads when inventory and assembly are run in
sequence. Memory stays bounded to streaming byte buffers plus path/hash metadata;
no graph is downloaded or duplicated. Assembly requires the same actual mounted
volume, not merely a JSON inventory copied from another device. The inventory
proves bytes at review time, not a future native rollback: native acceptance and
runtime rechecks remain required before activation.

## Reviewed request

All fields are required; unknown or duplicate JSON keys are rejected. Immutable
object descriptors have exactly `key`, `sha256`, and positive integer `bytes`.
The referenced catalog, catalog request and volume inventory use existing
`archive/sha256/<first-two>/<digest>` descriptors in a local SHA-named byte mirror.
The caller must explicitly lock the complete assembly request with its SHA256.

```json
{
  "schema": "anipals-runtime-index-request-v1",
  "catalog": {"key": "<immutable key>", "sha256": "<catalog SHA>", "bytes": 1},
  "catalog_request": {"key": "<immutable key>", "sha256": "<original request SHA>", "bytes": 1},
  "mode": "isolated-acceptance",
  "selected_regions": ["europe-germany"],
  "probes": {"europe-germany": [{"lat": 52.5, "lng": 13.4}]},
  "target_inventory": {"key": "<immutable key>", "sha256": "<observed inventory SHA>", "bytes": 1},
  "target_identity_sha256": "<reviewed deployment/volume identity SHA>",
  "previous_index_sha256": null
}
```

This illustrates shapes only; the sample coordinates are not verified activation
probes. Probes are reviewed inputs, not invented by the assembler, and must cover
exactly the selected rows. Existing `Composite` validation enforces 1–8 bounded
points inside each region, exact manifest/feature/image identity, selected R2
generation identity and local rollback ownership. Native readability still
requires the separate activation check.

## Local commands and artifacts

First inspect the actual mounted target volume. The regions JSON must list every
intended index selection, including remote additions, so existing ownership
states are included in the snapshot:

```sh
python3 tools/plan_runtime_index.py inventory \
  --volume-root "$ACTUAL_TARGET_VOLUME" \
  --regions-json "$EXACT_SELECTED_REGIONS_JSON" \
  --target-identity-sha "$REVIEWED_TARGET_IDENTITY_SHA256" \
  --output "$PRIVATE_WORK/target-inventory.json"
```

Review the observation, store its exact bytes under its SHA filename in the local
object mirror, and bind its descriptor in the reviewed assembly request. Then:

```sh
python3 tools/plan_runtime_index.py assemble \
  --request "$PRIVATE_WORK/runtime-request.json" \
  --request-sha "$REVIEWED_RUNTIME_REQUEST_SHA256" \
  --objects "$PRIVATE_WORK/objects-by-sha" \
  --volume-root "$ACTUAL_TARGET_VOLUME" \
  --output-directory "$PRIVATE_WORK/runtime-candidate"
```

The output directory is newly created with mode 0700; existing directories are
refused. It contains mode-0600 `index.json`, `coverage.json` and `review.json`.
The review binds index/coverage hashes, exact selected graph names and explicit
non-activation flags. Console output only gives aggregate counts, candidate hash
and status flags. Private source descriptors, target identity and paths are not
printed. No credential is required or read.

Before any actual publication or activation, review the complete source catalog,
real target-volume identity and retained bytes, immutable runtime-index hash,
isolated native cold/hot routes and explicit rollback/restart behavior. Preserve
the independent source-scope/endpoint acceptance gates. Missing real catalog
receipts, incomplete original migration, incomplete 129 supply or an unavailable
actual target volume are unmet dependencies; synthetic tests cannot supply them.
