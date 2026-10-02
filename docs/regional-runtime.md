# Isolated regional runtime

AniPals retains the current 61 independent Geofabrik extracts and the existing
163-city probe registry, of which 149 coordinates fall inside those extracts.
This scope is not worldwide road coverage. The repair
changes graph ownership and request execution; it does not concatenate regional
GraphIds, replace missing regions with nearby countries, or rebuild all graphs.

## Ownership and activation

Each immutable graph lives at `/data/regions/SLUG/FINGERPRINT/tiles`. Its identity
includes the pinned release, region, image and exact archive shard hashes.
The materializer reuses verified complete download parts, resumes partial parts,
streams split tar files directly into a private staging directory, validates the
entire regional tile inventory, then atomically publishes a completion marker.
Only a native-verified graph receives its region's explicit `active.json` pointer.
The router reads active pointers; unverified candidates remain inaccessible to
route requests. Symlinked roots and markers cannot alias another region's graph.

`Catalog` selects candidate regions from the official exact-extract polygons.
Both endpoints must fit the same extract. Multiple overlapping extracts may be
tried one at a time, each in a separate native request; their tile roots are never
combined. A journey for which no single extract contains both endpoints returns
an honest 404. A matching graph that is not ready produces an unavailable state.

## Process and memory boundaries

One lightweight Python HTTP router listens on port 8002. Each accepted graph
request launches the official `valhalla_service CONFIG ACTION JSON` actor with
one regional config. A semaphore bounds native concurrency to two requests;
each has an eight-second deadline and a hard 768 MiB `RLIMIT_AS` address-space
limit. The flat graph cache uses a **64 MiB soft target**, with LRU/hard eviction
disabled; it may exceed that target during a request. Search-label reservations
are reduced separately. Only the process limit provides the hard address-space
ceiling, not the cache target. Crashes and
timeouts terminate the isolated native process without contaminating another
region; request completion releases its slot. Shutdown drains/terminates child
process groups and reaps them. The OS may reuse file pages without a shared graph
namespace. This design avoids keeping 61 native engines resident.

`GET /status` reports installed and verified regions, graph fingerprints and the
built commit revision. Native verification must succeed after a router restart;
process liveness alone does not assert routing readiness. Local `/verify` checks
an exact candidate fingerprint before activation. Canada always requires both
Toronto and Montreal regressions. Other regions use an audited city probe, with
real pedestrian graph-node probes when city geometry yields no suitable edges;
a native crash, timeout or IO failure remains a failed verification.

## Migration and fixed release

The runtime uses the code-reviewed `deploy/tile-release.txt` tag rather than
GitHub's implicit latest release. Old mixed `.gph` files are never adopted.
Compatible cached archive parts may be moved into the new per-region cache.
After a new isolated graph passes native verification, a durable migration
journal authorizes moving/removing the legacy mixed graph; a restart must regain
native health before continuing deletion. Disk checks account for remaining
archive bytes plus extraction and reserve, without a third concatenated tar.

This cutover targets a fixed legacy release. Future version turnover needs a
separate draining and garbage-collection policy: old immutable roots must not be
removed while an in-flight request still reads them. Activation pointers alone
do not authorize collecting prior versions. See also the
[release and official coverage contract](regional-release-contract.md).

## Verification status

Local tests exercise real child-process crash/timeout/capacity handling, graph
activation and path isolation, geometry boundaries/holes/date-line pieces,
streaming extraction, download resumption, durable migration and release gates.
On macOS only, lifecycle tests bypass the Linux address-space wrapper because
Darwin refuses lowering that limit; Linux CI runs the actual wrapper.

[Linux CI run 36983580857](https://github.com/WILLJBS/anipals-tiles/actions/runs/36983580857)
for tiles commit `2799fcb` passed all 35 Linux tests and the real Valhalla 3.3.0
candidate-image gate. It downloaded the original complete Canada archive, ran
the structural validator and both historical city locates, executed final routes
under runtime limits, and verified that completed graphs cause no extra download.
The final routes were Toronto 0.362 km / 154 ms and Montreal 0.551 km / 165 ms.

The diagnostic matrix isolated Montreal's SIGSEGV to hard LRU eviction: both
768 MiB and 4 GiB hard-LRU processes failed, whereas original, soft-LRU and
flat-cache alternatives passed. Toronto passed all six configurations. See the
[source and matrix review](native-cache-review.md); no crash backtrace was taken,
so the suspected source-level use-after-free is not claimed as a confirmed stack.
The preliminary `--version` CLI probe failure was also corrected by obtaining
the native version via the supported `status` action with a regional config.

The verified image digest is
`sha256:68dd497fe2d837dda462c509dc84f9be19062189ba25f34d520546cbb89d329a`.
Render's fixed-digest cutover has begun without changing service cost settings.
**Production acceptance remains pending**: verify the deployed digest, complete
migration of all 61 independent regional graphs and the 163-city scan. Passing
Canada native CI does not establish that every region is installed or every
production city route is healthy.
