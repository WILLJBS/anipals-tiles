# Release-to-R2 migration and global additions build

This procedure reuses the original 61 independent regional graph bytes. It does
not rebuild them, merge their GraphIds, require an 87 GB local copy, publish a
composite activation index, or switch a running service automatically.

## Authenticated source and bounded traversal

`tools/migrate_release_r2.py` accepts a downloaded GitHub release JSON and READY
bytes. The existing supply gate checks the exact roster, immutable native image,
contiguous part sizes/SHA256, coverage provenance where present, and the single
explicit legacy READY exception. It will not treat a draft/subset as a complete
release. Root performs authenticated metadata retrieval; credentials are supplied
only in the process environment, never written into the repository or workflow.

For each region, the tool makes two passes through its original split tar:

1. Download **one shard**, verify its complete trusted size/SHA256, then expose it
   to streaming tar. Retain at most that shard and one tile. Build the complete
   regional header, tile-size and tile-SHA inventory; reject path escapes, links,
   special files, duplicate paths/GraphIds and header/path disagreement.
2. Repeat the authenticated shard stream. Match every tile against pass one,
   then run the **same** `gph-v3-index-v1` checker used by release builds with the
   complete first-pass header inventory, including cross-tile node/edge bounds.
   Only after this check can a tile be uploaded. Every upload is fully GET-read
   back and compared by size/SHA; multipart ETags are never used as content SHA.

`validate_tiles.validate_tile` is the shared checking layer; the directory-wide
validator delegates to it without changing its contract. No graph validation
logic is reimplemented in the uploader. Missing outside-extract references remain
counted as before; this structural walk is not a native route/topology proof.

Each source part is deleted after consumption. The hard per-tile guard is 512 MiB,
and the source-shard downloader reserves 640 MiB beyond the shard for the tile
and disk margin. Peak retained data is therefore one release shard plus one tile,
not all shards or all regions. A second source pass is intentional: a legacy
release lacks trusted tile hashes, and those hashes plus all target headers must
be known before selecting immutable R2 keys and validating cross-tile references.

## Pilot, publication and restart behavior

With `R2_ENDPOINT_URL`, `R2_BUCKET`, `R2_ACCESS_KEY_ID`, and
`R2_SECRET_ACCESS_KEY` injected in-process, run from a reviewed checkout:

```sh
python3 tools/migrate_release_r2.py \
  --release-json "$PRIVATE_WORK/release.json" --ready "$PRIVATE_WORK/READY" \
  --region north-america-canada --mode pilot --work "$PRIVATE_WORK/migration"

python3 tools/migrate_release_r2.py \
  --release-json "$PRIVATE_WORK/release.json" --ready "$PRIVATE_WORK/READY" \
  --region all --mode full --work "$PRIVATE_WORK/migration" \
  --pilot-receipt "$PRIVATE_WORK/migration/pilot-receipt.json"
```

The first command uploads and fully verifies **20 tiles**, then records the
release/image/coverage contract, bucket and verified count in a private receipt.
It publishes no region manifest. Full mode requires a matching pilot receipt.
A smaller region cannot fabricate a 20-tile success. `--region SLUG` can also
complete one region before `--region all`.

Tile keys are `navigation/graphs/SLUG/INVENTORY_SHA/tiles/PATH`.
The complete region manifest is published only after all tiles pass the second
walk and GET verification, at
`navigation/graphs/SLUG/INVENTORY_SHA/manifests/MANIFEST_SHA.json`.
Content-addressing the manifest separately allows identical tile bytes to acquire
new audited source/coverage provenance without overwriting an old manifest.
Composite release records already name its exact SHA and size; their loader
uses this immutable manifest path.

Object creation uses `If-None-Match: *`. A concurrent 412/conditional conflict
requires a complete GET/SHA verification of the winner before reuse; no overwrite
is attempted. Existing objects are fully GET-verified before reuse. Corruption is an explicit
failure, never a silent overwrite; AccessDenied is never interpreted as missing.
An interrupted run may leave verified, unreferenced tile objects. Retrying
reuses them, while repeating source verification. No manifest means no activation
permission. Source download progress itself is not durable across interrupted
passes; this is a safe idempotent object migration, not an instantaneous resume.
Private per-region receipts report manifest SHA/size and exact coverage feature
for a later reviewed composite index. They do not activate the old 61 regions as
remote graphs; the existing local roots remain available for rollback.

## Additions-only build workflow

`tools/global_coverage_plan.py` first audits frozen city/index/local coverage
bytes and records all three input SHA values. The historical granular audit
produced 138 ordinary extracts and 19 residual source rows. The final generator
coalesces nine Central America children (103 rows) into the shared regional graph:
**129 ordinary graphs / 1,120 rows + three shared/window graphs / 122 rows**.
The old 61 polygons retain 4,980 assigned rows. All 6,222 original source rows
are assigned exactly once; this count is not a distinct-city or route-success count.

`tools/prepare_global_build.py` verifies all three input hashes, reproduces the
original audit, then emits six repository inputs together:

- `deploy/global-additions.json`: the 129-entry matrix and scope-byte SHA;
- `deploy/global-additions-coverage.json`: exact official extract geometry,
  feature locks and required native probe hashes;
- `deploy/global-additions-scopes.json`: complete source-row ownership, 129 native
  probe sets, coalescing audit and source hashes;
- `deploy/global-gap-{sources,coverage,regions}.json`: three graph inputs with
  Central America expanded to 120 source probes and each technical window to one.

Regeneration requires the original frozen `--plan`, `--index`, `--cities` and
`--base-coverage`, plus `--gap-spec`, `--gap-coverage`, and `--output-dir deploy`.
Those audit inputs may remain private/local; cloud execution uses only the six
committed deploy files. `python3 tools/check_global_build.py` verifies their hashes,
full source-row ownership, point containment and exact native probe contracts
without any ignored local directory. Old local coverage bytes stay immutable.

Dispatch the existing `build.yml` at the reviewed commit with
`roster_file=deploy/global-additions.json` and
`coverage_file=deploy/global-additions-coverage.json`. Both paths must be simple
`deploy/*.json` paths; defaults remain the unchanged 61-region inputs. The shared
matrix and release-manifest gates consume those exact inputs, building only the
129 additions at at most six jobs and one native build thread each. Every assigned
source coordinate must pass actual native pedestrian routing before a regional
manifest is accepted. Proof hashes and exact source-row IDs remain bound through
READY publication and R2 migration; a missing probe prevents publication.

A countries subset remains a draft. A completed requested roster can publish its
own schema-2 READY; this does not change the production pinned release. No R2
credentials are needed in GitHub Actions: new release assets subsequently use
the same pilot/full migration tool with `--roster` and `--coverage` pointing to
these reviewed inputs. The old 61 graphs require no rebuild.

The separate three-graph workflow is described in
[global-gap-builds.md](global-gap-builds.md). Central America uses the practical
790 MB dated official extract, shared by 120 source rows. The other two graphs
use explicit technical windows with source SHA pinning before clipping; they do
not claim administrative island boundaries. Both build paths require source
identity, structural integrity and actual native route proofs. The inputs and
workflow exist; cloud build, R2 publication and production routes are not claimed
completed by these local checks.
