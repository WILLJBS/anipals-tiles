# Navigation tiles: local evidence and safe patch review (2026-10-02)

This patch is not a global navigation repair. It is saved on the local review
branch `codex/navigation-integrity`; no push, image publication, remote dispatch
or deployment was performed. The existing `.complete` disk path
remains unchanged; it does not clear or re-download 87 GB on restart.

## Root cause correction

The claim that Canada release bytes are intrinsically corrupt is disproved for
the reproduced Toronto/Montreal failures. The same pyvalhalla 3.2.0 (`cbabe7cfb`)
locate requests succeed against `/tmp/navwork/ex/tiles` (complete Canada graph),
but fail against `/tmp/navwork/localtiles/tiles` (mixed independent graphs):

| Point | Complete Canada | Mixed graph |
|---|---|---|
| Toronto 43.7064,-79.3986 | locate succeeds | DirectedEdge 47980,1,23756 exceeds3545 |
| Montreal 45.5088,-73.5878 | locate succeeds | NodeInfo48706,1,26571 exceeds148 |

The level2 source tiles are byte-identical in both roots, while their referenced
level1 tiles differ:

| Tile | Canada nodes/edges | Mixed nodes/edges |
|---|---|---|
| 1/047/980.gph |125196/313205 |1550/3545 |
| 1/048/706.gph |104282/240557 |148/397 |

Toronto level2 `2/000/769/362.gph` SHA256:
`4bd5980219b440c7d7e74cda1588985ba412ac44270e2a63ccbcfff6c69fdfdc`
in BOTH roots. Its referenced level1 tile has SHA256
`74880a661393fe0ed3c233f9969292fe807d491520e44b7e2b784042241e301c`
in Canada, and
`2eb744673ef8f8992d825031932af1739e7534181c5e786360679eaad0843feb`
in the mixed graph.

Independent region builds reuse GraphIds with different local node/edge indexes.
Overlay extraction preserves references from one graph but replaces their target
with another graph's bytes. A digest match to a release only proves transfer
integrity. It does not prove that independently built region graphs compose.
Reducing concurrency to1 is a memory-pressure experiment, not a proven cure.
An8-hour rebuild of the same per-country architecture will not fix this.

## Official-source audit and gate coverage

[Official export_edges source](https://github.com/valhalla/valhalla/blob/685e3d814675e2b692b8602c5709a76160b42fc4/src/valhalla_export_edges.cc)
iterates edge counts, but skips previously visited edges, transit, missing opposing
edges, shortcuts, ferries and unnamed edges; it does not enumerate every node's
edge span or every spatial-bin reference. A zero exit is not a full index proof.
Its shape-export output being discarded does not change that coverage.

The added `validate_tiles.py` accepts exactly the observed 3.3.0 tile version,
rejecting other versions rather than assuming all 3.x headers share its ABI.
It uses the little-endian V3 road ABI from official
[tag3.3.0 headers](https://github.com/valhalla/valhalla/tree/3.3.0/valhalla/baldr)
and checks every file header/end offset/section and bin offsets, every node edge
and transition span, edge end-node references and edge-info offsets, and every
spatial-bin edge reference to a present tile. Missing neighboring regional tiles
are counted, not claimed valid. Transit/unknown ABIs fail closed. This gate does
not validate variable restriction bodies, text grammar or routing topology.

Mechanical results:
- Complete Canada10336 tiles: passes; external references0.
- Three Toronto/Montreal/Quebec area files in ex2: passes with missing neighbors
  counted161790; ex3 US sample passes with241762 missing neighboring references.
- Mixed localtiles: fails `0/002/999.gph: nodes index2200 out of bounds1564 in tile23984`.
- Synthetic valid V3 tile: passes. Node-span poison, bin-reference poison and
  truncated tile: each fails. Full release gate also rejects conflicting path
  hashes across otherwise valid region manifests BEFORE producing READY.

## Safe patch behavior and local verification

- Exact complete part size AND SHA256: skip Range entirely (zero HTTP requests).
- Partial part: Range resume; ignored/unsupported Range gets one bounded fresh
  download, then size/SHA256 validation. Oversize and same-size-corrupt files are
  removed before fresh download. Bad fresh content is deleted and never marked done.
- Download plans require the exact61-region roster, positive sizes, SHA256 digests
  and contiguous part sequences. The current legacy READY release metadata still
  passes this transfer plan; existing `.complete` disks bypass it as before.
- One pinned digest file feeds BOTH build containers and Docker runtime build ARG.
  Changing it deliberately requires ABI review and a compatible graph rebuild.
- Subset dispatch remains a diagnostic draft: final production publication is
  skipped. Rescue publication uses the identical exact-roster/provenance/hash/
  collision manifest gate, with no caller-controlled lower country threshold.
- Tar extraction occurs in staging with path/link checks. All target collisions
  are checked before graph changes, under an exclusive `fcntl` install lock;
  each file then becomes visible via atomic rename. The lock spans preflight and
  install, so concurrent downloader workers cannot overwrite each other after
  racing through an unlocked precheck. Failed stages are removed.

Run `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v` from this
repo: six tests cover the real curl/localhost Range boundaries, malformed release
plans, legacy plan compatibility, index poison and installation locking/collision.
The local HTTP server is closed/joined in `finally`; no service is left running.
All three workflows parse as YAML; every run block passes `bash -n` after expression
substitution; both shell scripts pass `sh -n`. Docker/CI/image/remote routes were
not exercised. There is no claim of ready-to-publish global navigation supply.

Main-agent verification independently repeated the same Toronto/Montreal
locate requests and target-header/SHA comparisons. The strict 3.3.0 gate passes
all 10336 Canada tiles with zero external references and rejects the mixed root
at the reproduced cross-tile node-index inconsistency. Six tests were rerun after
adding an explicit unsupported-3.4.0 rejection case.
The pinned image's binary version has not been executed locally: identical
build/runtime digest configuration is not evidence of graph-reader compatibility.
Verify that version and representative original-region reads before deployment.

## Two complete architecture paths for the global repair

| Path | Concrete dependencies | Advantages | Risks |
|---|---|---|---|
| Isolated regional graphs + bounded engine router | Keep each region's tiles/config in its own immutable root; publish coverage polygons and graph fingerprints; add a lightweight8002 router; map API city slug/location to region using shared contracts; bound native workers with LRU, per-worker memory budget, lease/refcount for in-flight calls, warmup timeout and draining; try both containing regions near boundaries and use same-region origins/destinations; add exact cross-region fallback semantics; no regional .gph overlay. | Reuses existing region assets, bounds memory, failures isolated, can validate Toronto/Montreal immediately per graph. | Cross-region journeys require an explicit product decision or a separate combined corridor graph; incomplete regions must not be hidden by coordinate guesses; workers must not be evicted while busy. Current API contracts/adapters need a coordinated change across repos before deployment. |
| One graph built from one unified input set | Deduplicate and merge all chosen PBF extracts first; one Valhalla graph build with one ID namespace; calculate PBF+intermediate+tile+tar/swap disk peaks from actual bytes; require suitable runner RAM/disk and version pin; validate the final unified graph and partition only its already-built files; signed region inventory and SHA256 release manifest. | One consistent GraphId namespace and cross-region routing; existing8002 service/API protocol can remain simple. | Existing14GB Actions runner and country matrix do not establish adequate capacity. Extra CI hardware/spending requires user decision; build/recovery must be checkpointed and validated. Independent already-built graphs cannot be converted into this by tar concatenation. |

The collision gates here are isolation safety measures. They deliberately stop
an incompatible graph publication/install. They are not the final architecture
and must not be presented as restoring worldwide coverage. The61-region scope is
unchanged; the12-region geographic gap remains a separately tracked decision.

## Regional architecture implementation update

The historical cross-region collision rejection above applies to the unsafe
overlay architecture. The replacement release contract is now
[isolated regional schema 2](regional-release-contract.md): retain all regional
structural and transfer validation, allow cross-region path/hash differences,
and bind exact official Geofabrik polygons to each separate graph manifest.
Full graph builds and release rescue require explicit dispatch; image candidates
use reviewed SHA tags and native route gates rather than silently moving latest.
This update describes code changes; it does not assert that remote deployment or
all 61 native regional route checks have completed.

### Native correlation evidence after a scope gate (2026-10-04)

The build now runs the existing independent scope diagnostic before graph cleanup
for source rows with anomalous native attempts, including a row whose later offset
passed. Its selection verifies the gate's scope hash and graph fingerprint. The
original smoke return status and 0 < length < 5 acceptance rule remain unchanged;
diagnostic output cannot create a native acceptance proof.

The allowlisted artifact retains native projected-distance extrema and polyline6
first/last endpoint offsets, point count and endpoint equality. Missing/invalid
geometry is explicit, not a zero-distance default. Coordinates, encoded shapes,
OSM edge IDs and native raw payloads remain process-local. ABI/error status/code
fields retain existing mappings. Every fact is bound to the frozen source-row
identity and actual graph fingerprint; distances do not by themselves approve a
public destination or change its position.

Valhalla documents [locate projections](https://valhalla.github.io/valhalla/api/locate/api-reference/)
and [six-digit shape precision](https://valhalla.github.io/valhalla/api/decoding/).
Offline regressions cover projected distances, exact polyline6 decoding, duplicate
endpoints, malformed inputs, no coordinate disclosure, unchanged native error
mapping and the actual shell's preservation of a failed gate exit status. Real
Bakwa/Lander correlation results still require the next two-region diagnostic run.


The subsequent Venezuela run supplied independent route-shape evidence, but
its locate projection counts were invalid because the parser used an internal
member name rather than the 3.3.0 JSON fields. See the
[protocol correction and real native gate](native-scope-diagnostics.md#locate-protocol-correction-and-native-gate-2026-10-04).
Do not use the old zero counts as projection absence or recompute historical
results without the original response bytes. The route-shape offsets are
independent of that parser error.
