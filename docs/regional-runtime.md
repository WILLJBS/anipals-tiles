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

## Retired graph lifecycle — included in the live image

`regional_gc.py` in the live image serializes activation per region and durably journals the
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

Implementation, Linux CI and deployment of these lifecycle changes have passed.
Real-process tests verify the lease and deletion boundaries. Production still
uses the same release: no new-release retirement/deletion is claimed to have
occurred there. All 61 regions completed migration and activation checks. The existing
mixed-root migration is a separate one-time cleanup.
See also the [release and official coverage contract](regional-release-contract.md).

## Verification status

Local tests exercise real child-process crash/timeout/capacity handling, graph
activation and path isolation, geometry boundaries/holes/date-line pieces,
streaming extraction, download resumption, durable migration and release gates.
On macOS only, lifecycle tests bypass the Linux address-space wrapper because
Darwin refuses lowering that limit; Linux CI runs the actual wrapper.

[Linux CI run 36986181555](https://github.com/WILLJBS/anipals-tiles/actions/runs/36986181555)
for tiles commit `c68d16915e266193539595923b1a186b61277c8e` passed all 49 Linux
tests and the real Valhalla 3.3.0 candidate-image gate. It downloaded the original
complete Canada archive, ran the structural validator and both historical city
locates, executed final routes under runtime limits, and verified that completed
graphs cause no extra download. The final routes were Toronto 0.362 km / 175 ms
and Montreal 0.551 km / 191 ms.

The diagnostic matrix isolated Montreal's SIGSEGV to hard LRU eviction: both
768 MiB and 4 GiB hard-LRU processes failed, whereas original, soft-LRU and
flat-cache alternatives passed. Toronto passed all six configurations. See the
[source and matrix review](native-cache-review.md); no crash backtrace was taken,
so the suspected source-level use-after-free is not claimed as a confirmed stack.
The preliminary `--version` CLI probe failure was also corrected by obtaining
the native version via the supported `status` action with a regional config.

Production pins the tested image digest
`sha256:3428cba734d6cca4f03ed9eb36e2c2fb70ad304ba58b1b70986b0875dae77ac0`.
It includes native graph leases, retired-graph GC and catalog handling for a read
of an old activation pointer. At the 2026-10-02 08:54 UTC deployment restart,
completed graphs were individually reverified and reported installed within tens
of seconds, without RESET or downloading completed graphs again. The final
checkpoint is **61/61 regions installed**, followed by `all graphs materialized`.
No native failure or extra router restart was observed after that deployment
through acceptance. API `fa38cfd` is live and web/play at that commit are READY.

**Migration and city-scan acceptance passed.** All 163 registered centers were
classified: 14 are outside official extract polygons; 148 covered centers now
return actual routes after the app's city-alias relation repair. Dubai's center
is outside the app's 2,500m reviewed-place gate, so its 404 is preserved and an
approved park separately verifies the city with a 642m route. All 149 covered
cities thus have a successful route; this does not mean every original center
probe passed. The intermediate `3588b1a` candidate was not promoted to production.

Same-OD graph-change/outage/recovery regression used actual read-only production
city queries and controlled engine responses. It verified fresh routes, honest
failure and released DB leases without disrupting production. The original scan,
three repaired-center reprobes, Dubai venue probe and region install timestamps
remain separately recorded. On 2026-10-03, closeout checks confirmed the same live
image, API readiness and Toronto's 362m route; no new deployment was required.
