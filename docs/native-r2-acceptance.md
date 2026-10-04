# Actual R2/native cold and hot acceptance

`native-r2-acceptance.yml` is an explicit, private, single-region diagnostic. It never deploys, registers a release, changes production pointers, rebuilds PBFs, or downloads a complete graph. A successful run proves the reviewed route through the named complete regional graph. It does not establish all cities or all destinations. The existing local-byte `native_remote_smoke.py` remains a separate native HTTP semantics gate.

## Prerequisites and dispatch

The migration must first finish a full region, GET-verify every uploaded tile and its manifest, and publish the complete private region receipt. A twenty-tile pilot is insufficient. The whole source release must be READY under its registered migration contract; the incomplete additions release cannot bypass that requirement.

The workflow has five inputs: `source_sha`, `bucket`, `input_key`, `input_sha`, `input_bytes`. Dispatch the matching reviewed ref. Runner checkout SHA, GitHub run SHA and workflow SHA must match `source_sha`. The request itself must be a verified private content-addressed object at `archive/sha256/<sha-prefix>/<sha>` with the supplied SHA and positive byte count no larger than 65,536. No shell or executable is accepted in it.

Three existing repository encrypted secrets supply the endpoint and S3 access pair. The existing endpoint/bucket validator runs before requests. This creates no credential; the keys may have broader access, while the native process only reads graph objects. The host publishes diagnostic results and logs in a distinct private namespace.

## Exact private request schema

All fields below are required, extra fields rejected. Placeholder hashes below must be replaced with real verified values; this example is not runnable.

```json
{
  "schema": "anipals-native-r2-request-v1",
  "diagnosticSha": "<reviewed diagnostic 40-character commit>",
  "migrationSha": "<exact migration 40-character commit>",
  "contract": "original61",
  "tag": "<complete release tag>",
  "slug": "<one exact registered region>",
  "graphFingerprint": "<64-character graph inventory SHA>",
  "release": {"key":"archive/sha256/<prefix>/<sha>","sha256":"<sha>","bytes":123},
  "ready": {"key":"archive/sha256/<prefix>/<sha>","sha256":"<sha>","bytes":123},
  "probe": {"key":"archive/sha256/<prefix>/<sha>","sha256":"<sha>","bytes":123},
  "receipt": {
    "key":"navigation/migration-receipts/<migrationSha>/<tag>/receipt-<slug>/<sha>.json",
    "sha256":"<sha>","bytes":123
  },
  "manifest": {
    "key":"navigation/graphs/<slug>/<graphFingerprint>/manifests/<sha>.json",
    "sha256":"<sha>","bytes":123
  }
}
```

`contract` is exactly `original61`, `gap3` or `additions129`. `release` is the original complete, fully paginated release metadata used by migration. `ready` contains the exact READY bytes. Upload these and the probe with the existing immutable private archive publisher; retain their full GET-verified descriptors. Request descriptors accept only the fixed content-addressed namespaces, never URLs or filesystem paths. Maximum release/READY/probe/receipt/manifest sizes are respectively 16MiB/4MiB/8KiB/8MiB/32MiB. Sizes are strict positive integers, so booleans and NaN fail.

Probe schema is `{ "schema":1, "slug":"<region>", "provenance":"<specific native success evidence or source-backed endpoint evidence>", "payload":{"costing":"pedestrian","locations":[{"lat":<latitude>,"lon":<longitude>},{"lat":<latitude>,"lon":<longitude>}]}}`. Both real endpoints must be distinct and inside the coverage polygon. Only lat/lon are accepted; radius, costing options and snapping overrides are refused. The probe is an explicit review input, not derived by silently moving a failed city center. Canada's already-tested Toronto pair is `(43.7064,-79.3986) → (43.7084,-79.3966)` from `tests/native_remote_smoke.py`; use only when a complete Canada receipt exists. Existing scope proof summaries do not record which offset succeeded and cannot reconstruct that endpoint.

## Verification and runtime bounds

`tools/native_r2_ci.py` uses `PrivateStore` to SHA-verify the fixed request and three input objects, then executes only hardcoded Docker commands as argument arrays. It never passes private input through shell interpolation. Each subprocess writes directly to a private temporary log. The complete exact checkout is mounted read-only; the input directory is read-only and only the dedicated result directory is writable. The container has no production volume or Docker socket.

`tests/native_r2_acceptance.py` verifies the entire release using `validate_profile_supply`, including roster, coverage, pinned image and part hashes. The region receipt must bind the current bucket and exact migration contract/checkout SHA. Receipt SHA and size lead to a separately pinned manifest SHA and size, exact graph fingerprint, ABI, feature hash, tile inventory and release part list. Schema 2 READY's graph fingerprint is compared when present. The sole existing grandfathered legacy READY exception remains unchanged in the shared validator. Partial releases and pilot receipts fail closed.

Native reads reuse `regional_r2.reader`, `ObjectCatalog`, `ObjectCache`, `Bridge` and `Engine`. The reader uses the same actual SDK client and unchanged runtime 2s connect/3s read/3 total attempts. An SDK before-send handler counts real GetObject HTTP attempts, including retries, without inspecting request data. Metadata requests are excluded from per-route counts. No arbitrary R2 tile key is allowed outside the verified graph inventory.

The native status action must report 3.3.0 without warming tile cache. A fresh 512MiB bounded cache serves the cold route, which must perform positive real R2 GET attempts, object reads and byte transfer. Each tile is SHA/size verified before native consumption. The identical hot route must add exactly zero GET attempts, object reads and source bytes. Each native process retains the existing 8-second deadline and 768MiB RLIMIT_AS. Both routes require nonempty geometry, positive distance below 5km, identical length and geometry, and zero bridge failures. Config keeps `tile_dir` empty for native URL loading; any unmanaged `.gph` file or `active.json` fails the gate.

Local markers and leases live only in a fresh diagnostic temporary directory; no production graph is activated. The container is uniquely named and removed in a finally block, including when the Docker client times out. Candidate build, diagnostic build, native execution and cleanup are bounded to 600/300/180/30 seconds; workflow step/job ceilings are 20/25 minutes. Failed checks remain failures; there is no relaxed retry route or no-route fallback.

The gate also decodes actual polyline6 endpoints with the shared
`native_scope_geometry` implementation. Each endpoint must be within
`min(500 metres, requested origin-to-destination distance / 4)` of its request;
collapsed endpoints and malformed geometry fail even if the native summary says
the route has positive length. Private records retain numeric offsets and the
limit, not coordinates or shapes. This uses the existing short-probe acceptance
rule, not a changed native snapping radius. Earlier cold/hot receipts without
these fields establish transport/cache behavior only; they must be rerun before
claiming this strengthened endpoint acceptance. Offline negative controls
reproduce distant but otherwise valid short routes passing the former gate.

## Private result and public output

On completion or an execution failure, the host writes a bounded private log tail (up to 512KiB with full-byte-count/truncation fields) and a result to `navigation/native-acceptance/<request-sha>/{logs,results}/<content-sha>.json`. Existing `PrivateStore.save_json` performs conditional immutable writes and full GET/SHA verification. Full native result, request descriptor, GitHub identity and phase/error class remain in that private result. If the request is invalid, no execution starts; if private result publication fails, the workflow fails without claiming completion.

Public output contains only complete/not-complete, an opaque content-addressed result descriptor, `productionActivated:false`, and on success cold/hot counts and timing. No logs, source object keys, coordinates, bucket endpoint or credentials become Actions artifacts. Request and SDK error messages are not echoed. A success receipt alone is not independent CI proof: acceptance also requires the matching GitHub run to conclude success.

## Dependencies and build inputs

Production `deploy/Dockerfile` is unchanged. `tests/Dockerfile.native-r2` derives from the exact locally built candidate, adds git and the existing pinned SDK requirements under `/opt/diagnostic-sdk`, and is never pushed. Its only COPY input is `tools/storage-requirements.txt`; package install settings are scoped to the install command. Diagnostic code, all existing runtime helpers and the registry are read from the clean, reviewed checkout mount. No ignored `.local` file is a cloud input. There are currently no root or production-Dockerfile-specific Docker ignore files; any future diagnostic ignore file must retain the pinned requirements input. SDK and noninteractive-install/timeout policy tests register this workflow and Dockerfile explicitly.

The new `tests/**` files also fall under the existing staging image workflow filter. That normal Canada test remains unchanged; this explicit R2 diagnostic itself does not invoke `native_smoke.py` or redownload Canada. Changes here do not automatically dispatch cloud storage access.

Run offline regressions with `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_native_r2*.py'`; install `tools/storage-requirements.txt` first for the full suite. Offline tests prove rejection behavior and argv/cleanup/private-output boundaries; they never claim actual R2/native acceptance. At implementation time no complete region receipt had been supplied, so no real diagnostic was dispatched.
