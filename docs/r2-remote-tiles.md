# R2-backed independent regional graphs

Status: opt-in runtime integration and native candidate-image gate; not deployed. The production runtime
still uses its 61 verified local graphs. Adding these modules does not activate
remote graphs, upload objects, extend coverage, or establish global acceptance.

## Fixed native compatibility

The reviewed **Valhalla 3.3.0** `GraphReader` reads `mjolnir.tile_url`, requires
`{tilePath}`, and passes its `tile_dir_` to `GraphTile::CacheTileURL` after a local
miss ([tagged source, lines 475–493 and 601–616](https://github.com/valhalla/valhalla/blob/3.3.0/src/baldr/graphreader.cc)).
The tagged `GraphTile` `store` helper writes only if `cache_location` is nonempty;
`CacheTileURL` then constructs the in-memory tile from the downloaded bytes
([tagged source, lines 190–226](https://github.com/valhalla/valhalla/blob/3.3.0/src/baldr/graphtile.cc)).
Therefore a remote request uses empty `tile_dir`, `tile_extract`, and
`traffic_extract`, with a loopback `tile_url`. Only the bridge manages disk bytes.
Master-only authentication/options are not assumed to exist in this native ABI.

`native_remote_smoke.py` is included in the candidate image and invoked by the
existing native promotion gate after local Canada routing. It hashes the actual
validated Canada graph, serves its declared objects through the bridge, and runs
Toronto/Montreal cold and hot routes with the existing eight-second deadline and
768 MiB native address-space bound. Hot requests must make no object-source call;
no native `.gph` cache files may appear. This is an executable CI gate, **not a
claim that it already passed CI**. Its source is local files; actual R2 latency,
credentials, outage behavior and production consumers still need separate proof.

## Object and cache contract

`regional_objects.py` accepts raw manifest bytes only together with their trusted
release SHA256 and expected builder digest. A schema-1 object manifest contains:

- `slug`, `graph_fingerprint`, `image`, `coverage_sha256`, `validation: gph-v3-index-v1`;
- `tiles`: regional relative `.gph` paths mapped to exact `size` and `sha256`.

The graph fingerprint is the canonical hash of that region's path→SHA256 map,
matching the schema-2 build inventory convention. Each object key is
`navigation/graphs/SLUG/FINGERPRINT/tiles/TILE_PATH`. There is no global GraphId
namespace. The release/activation identity must also bind builder and coverage;
an object fingerprint alone is not permission to activate a graph.

`regional_object_cache.py` is a single-owner, content-addressed cache with an
explicit byte budget and free-disk reserve. Each HTTP response first verifies
exact bytes. Truncation, oversized responses, SHA mismatch or interrupted fetch
never publish an object. Atomic replacement exposes only complete files. A shared
mutex covers fetch and response consumption so cache eviction cannot hide bytes
retained by open readers. This deliberately serializes object transport, while
native processes retain their existing independent concurrency bounds. Startup
removes abandoned private partials, rejects symlinks/unexpected entries, and
trims verified object files to the configured budget. Only this dedicated cache
is reclaimable; existing activated local graph roots remain under lease-based GC.

`regional_object_bridge.py` binds only IPv4 loopback on a dynamically chosen port.
It returns 404 only for a tile outside the trusted inventory; fetch/integrity
failures increment a visible failure counter and return 503. Each native request now receives a unique bridge URL token. Transport/integrity
failures attach to that token and override native no-route errors with 503, even
if native exits with error 442. Concurrent requests cannot inherit each other's
failure. Per-request config files and tokens are removed after child completion.
Across overlapping candidates, a real unavailable graph remains 503 if no other
candidate succeeds; a later no-route result cannot conceal that outage.

`regional_r2.py` uses boto3 SigV4 with HTTPS, `auto` region and three SDK attempts.
Runtime values are `R2_ENDPOINT_URL`, `R2_BUCKET`, `R2_ACCESS_KEY_ID`, and
`R2_SECRET_ACCESS_KEY`; no values or signed object URLs are logged or persisted.
The image includes `python3-boto3`. Inventory/index writes and runtime activation
are intentionally outside the reader's responsibilities.

## Composite release and opt-in runtime

`regional_composite.py` loads `navigation/releases/SHA256/index.json` only when
`ANIPALS_REMOTE_INDEX_SHA256` pins that exact index. Its schema-1 contract binds
`image`, `local_coverage_sha256`, and `remote_regions`; each region declares its
slug, object graph fingerprint, exact feature, manifest SHA/size and one to eight
native activation probes inside that feature. Manifest paths derive exclusively
from validated identities. The manifest itself binds the exact feature SHA.
Existing local region names cannot be replaced. Cached supply is reused only
with exact SHA validation; corrupt metadata fails closed.

`regional_remote_runtime.py` is enabled only by that environment variable. It
adds the trusted remote polygons to Catalog, prechecks a declared object, checks
all supplied native activation probes, then uses the existing atomic activation
and FD-lease lifecycle. The immutable activation fingerprint binds **manifest
SHA and native image**, distinct from the per-tile inventory fingerprint used in
R2 paths: new provenance cannot mutate an old graph's marker or lease identity.
An index-only membership change preserves unchanged regional identities.
The unchanged local release worker continues to materialize/reverify the old 61
regions independently. Missing/unavailable remote candidates remain 503.

The default object budget is 4 GiB; explicit configuration allows 64 MiB–16 GiB,
with a separate 256 MiB free-disk reserve. No paid disk or instance expansion is
performed. Enabling a release requires real cloud and native acceptance first.

`.github/workflows/remote-tiles-diagnostic.yml` is dispatch-only, accepts an exact
source SHA, builds locally and runs all unit plus actual native tests. It does
not publish images or deploy. It reuses only the existing Canada archive assets;
it does not rebuild PBFS or download the other 60 regions.

## Global coverage review and remaining cloud gates

The initial 140-extract proposal was audited into 138 granular official
extracts and 19 residual source rows. The final executable plan coalesces nine
Central America children into its shared parent: **129 ordinary additions
(1,120 rows), three shared/window graphs (122 rows), and the existing 61 graphs
(4,980 rows)**. All 6,222 source rows have one explicit owner in committed
`deploy/global-additions-scopes.json`. The Central America native scope contains
120 rows; Port-aux-Français and Grytviken each require one route probe. This is
build planning, not proof that those cities currently route successfully.

The shared Central America polygon is the unchanged official extract. The other
two use declared technical extraction windows within pinned parent sources,
not administrative island boundaries or whole-archipelago claims. All new graph
features bind their exact native probe SHA and source-row IDs; native proof is
required by the common regional manifest/READY/runtime supply gate. See
[global-gap-builds.md](global-gap-builds.md) and
[release-r2-migration.md](release-r2-migration.md) for generation and dispatch.

Country labels cannot substitute for polygons; both route endpoints must fit
one isolated graph. The 6,222 source rows also include duplicate identities and
small settlements, so that number is not a validated distinct-city count.
`tools/check_global_build.py` checks every committed scope without depending on
ignored local planning files. Local graph candidates remain ahead of overlapping
remote graphs, and independent graph tile files are never overlaid.

Existing 61 graph bytes should be reused without rebuilding. Their old exact
coverage features must remain bound to their graph provenance; refreshing the
entire bundled index would change historical feature hashes. The composite index preserves that local coverage hash and adds individually
bound remote features without weakening the existing exact-roster READY check.

Before switching production: publish and read back object bytes; produce the
reviewed pinned release index; run the native CI and real R2 cold/hot route gates
under the existing deadline; then mechanically report every reconciled city and
genuine unroutable case. Code exists for authenticated index consumption, native
activation, region leases and request-specific R2 failure handling; local tests
do not establish actual cloud operation. Passing local tests or polygon inclusion is not global navigation success.


## Replacing existing local regions

The original schema-1 composite only adds remote regions. Reviewed schema-2
indices can replace existing local regions through the shared
[storage ownership and explicit rollback contract](regional-storage-ownership.md).
They pin the supply catalog and preserve exact baseline feature hashes, retain
an explicitly reviewed local generation, prevent stale local/remote writers and
make GC honor that retained generation. The index is opt-in; migration receipts
or an offline supply catalog do not activate it. Selecting R2 can create a
regional unavailable interval until native candidate verification completes.
