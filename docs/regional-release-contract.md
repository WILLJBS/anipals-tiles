# Isolated regional graph release contract

The 61 existing Geofabrik extracts are independent graphs. A tile path or
GraphId is scoped to one region; different regions may have different bytes and
node/edge indexes at the same path. No global overlay is permitted. Existing
legacy releases may only be migrated into separate immutable regional roots,
with per-region validation, never installed on top of the old shared root.

## Official coverage source

`deploy/coverage.json` is a GeoJSON FeatureCollection generated from the official
[Geofabrik index](https://download.geofabrik.de/index-v1.json). Each feature maps
one `deploy/regions.json` entry to the **exact PBF URL used by build.yml**.
Properties preserve `slug`, `region`, official `source_id`, `pbf_url`, and the
SHA256 of the downloaded index bytes. Polygon/MultiPolygon coordinates, rings,
holes and antimeridian pieces are copied without simplification.

The initial frozen snapshot matches all 61 URLs. US West, South, Midwest,
Northeast and Pacific each have their own official `us-*` feature and own
`north-america/us-*-latest.osm.pbf`; these are not inferred state unions.
US Pacific includes separate pieces on both sides of ±180° longitude. Runtime
selection must use the actual geometry and preserve holes/date-line pieces,
not a bounding box or country-name heuristic. Both route endpoints must belong
to the same available regional root. Coverage describes the extract envelope,
not a guarantee that a routable road exists at every point inside it.

Refresh deliberately from a downloaded official snapshot:

```sh
curl -fL --retry 3 https://download.geofabrik.de/index-v1.json -o /tmp/geofabrik-index.json
python3 tools/coverage.py --index /tmp/geofabrik-index.json
python3 tools/coverage.py
```

Review the geometry/provenance diff with the associated extract update before
publication. `coverage.py` rejects missing/ambiguous URLs, roster substitutions,
open rings and invalid coordinates. A new index must not silently replace
coverage for an old graph build: regional manifests bind their exact feature
hash and PBF URL, and READY rejects mismatches.

## Manifest schema 2

Each `manifest-SLUG.json` records the pinned builder image, regional structural
validation report and complete per-tile SHA256 inventory, contiguous archive
parts, PBF URL and feature hash. The full READY gate still requires the exact
61-region roster, the expected validator, nonempty consistent tile counts,
legal relative tile paths, SHA256 hashes, and published part sizes/digests.
The gate intentionally does not compare tile hashes between different regions.

READY includes `schema: 2`, `graph_layout: isolated-regions-v1`, `image`,
`coverage_sha256`, `regions`, top-level `parts`, and `region_manifests` keyed by
slug. Each region record has `graph_fingerprint` (canonical JSON SHA256 of its
own tile inventory), `coverage_sha256`, validator, tile count and parts.
Canonical hashes use UTF-8 JSON with sorted object keys and compact separators.
The bundled `coverage.json` hash must match READY before using that release.
This is an integrity/provenance contract, not a substitute for native route tests.

Legacy manifests lack coverage provenance and do not pass new READY publication.
Reusing old archive bytes requires validating each isolated archive and producing
new regional manifests under the new contract; missing facts are not invented.

## Workflow boundaries

- `build.yml` only runs on explicit workflow dispatch. Ordinary code/workflow
  pushes cannot launch an hours-long 61-region build. A subset remains a draft.
- `publish-ready.yml` only runs on dispatch with an exact draft tag. It no longer
  selects the newest draft or pushes diagnostics back to a source branch.
- `deploy-image.yml` accepts an explicit commit SHA, or source changes pushed to
  `staging` / `codex/navigation-integrity`. It validates coverage, builds one local
  candidate, runs its actual native binary version and Canada route smoke, then
  pushes the **same tested image** with immutable `sha-SHA` and `staging-SHA` tags.
  It never tags `latest` and never directly promotes a production service.
- Native smoke is owned by `tests/native_smoke.py`, copied by the runtime Docker
  build to `/usr/local/lib/anipals/native_smoke.py`. Failure blocks publication.

No build/dispatch/push/deployment is implied by local tests. Deployment acceptance
must separately prove native ABI compatibility, isolated regional installation,
actual Toronto/Montreal routing, endpoint polygon filtering and bounded process
behavior against the exact promoted image/release.
