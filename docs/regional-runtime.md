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

## Retired graph lifecycle — candidate `c68d169`, awaiting CI/deployment

The live image described below does not yet include the new GC implementation.
Local `regional_gc.py` serializes activation per region and durably journals the
previous active fingerprint in `retired.json` **before** switching `active.json`.
GC considers only these explicitly retired graphs, rechecks that each is no
longer active, and does not infer deletion permission from directory age, disk
pressure or the presence of an unactivated candidate.

A native request holds a shared file-lock lease on its graph. The child inherits
the lease file descriptor, so a router crash does not release protection while
the native reader still runs. GC requires a nonblocking exclusive lease; busy
graphs remain queued. An eligible graph is atomically renamed to a deletion
tombstone before removal, allowing interrupted deletion to resume. The boot
supervisor retries collection every 30 seconds, in addition to materializer
collection points. Old versions are reclaimable after their readers finish;
this is not an indefinite rollback archive. If a catalog read races with retirement,
it retries once only after proving that the active fingerprint changed. Missing or
corrupt current graphs still fail explicitly; large deletions never block catalog reads.

These lifecycle changes still require CI, deployment and operational acceptance.
Until then, do not assume old regional versions are automatically reclaimed in
production. The existing mixed-root migration is a separate one-time cleanup.
See also the [release and official coverage contract](regional-release-contract.md).

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
Production now pins that exact image digest without a service-cost change.
Canada (`north-america-canada`) and US South (`north-america-us-south`) are confirmed
installed; API `68fa5da` is live and web/play `68fa` is READY. **Full migration and
coverage acceptance remain pending**: all 61 independent graphs must finish
migration and the 163-city scan must classify the 149 coordinates within extract
envelopes and 14 outside. Envelope membership does not itself prove a route.
The GC and catalog candidate `c68d169` passes all 49 local tests and is awaiting
Linux native CI/deployment. It is not included in this live image or its 35-test CI result.
