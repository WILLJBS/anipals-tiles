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
each has an eight-second deadline and 768 MiB address-space limit. Graph-cache
and search-label reservations are bounded in the generated config. Crashes and
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

The candidate-image gate downloads the original complete Canada archive, runs
the structural validator, executes the pinned native binary under runtime limits,
checks both historical city locates and real routes, and verifies that completed
bytes cause no additional download. At the time of this document's update,
**native container CI and production cutover are pending**. Local mocks and a
matching image digest do not prove native ABI compatibility or deployed coverage.

The first Linux candidate build and all tests passed, but the standalone
`valhalla_service --version` probe exited because the pinned 3.3 CLI treats its
first argument as a config filename. The 3.3 source confirms direct-request mode
is supported; the gate now obtains the version through its native `status` action
with a real regional config, before both regression routes. No image from the
failed gate was published or deployed.
