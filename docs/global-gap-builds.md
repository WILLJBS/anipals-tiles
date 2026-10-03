# Completing the 19 residual source-coordinate gaps

The residual rows now have concrete build inputs in
`deploy/global-gap-sources.json`, their exact routing envelopes in
`deploy/global-gap-coverage.json`, and a separate three-graph roster in
`deploy/global-gap-regions.json`. These inputs do not modify the old 61 graphs
and complement the final 129 ordinary additions. They are executable build
inputs, **not yet built/deployed graphs or successful-route evidence**.

| Independent graph | Source snapshot | Source bytes | Assigned source rows |
| --- | --- | ---: | ---: |
| Central America | `central-america-261002.osm.pbf` | 790,657,004 | 120 |
| Port-aux-Français technical window | `australia-oceania-261002.osm.pbf` | 1,570,208,020 | 1 |
| Grytviken technical window | `south-america-261002.osm.pbf` | 4,132,357,038 | 1 |

The official Central America extract is a practical shared graph for all 17
Caribbean coordinates and 103 additional source rows from nine child extracts.
Those nine builds (Bahamas, Belize, Cuba, El Salvador, Guatemala, Haiti and the
Dominican Republic, Honduras, Jamaica and Nicaragua) are removed from the
ordinary additions roster. This produces 129 ordinary graphs plus three shared
or window graphs, without duplicating their build/storage. Its polygon is
copied unchanged from the frozen Geofabrik index. Existing installed local
country graphs are attempted before an overlapping remote aggregate, preserving
the old graph's latency and isolation. A request always uses one graph root;
country and aggregate GraphIds are never combined.

The other two use explicitly declared **technical extraction windows**, centered
on the source settlement coordinate with ±1° latitude and ±1.5° longitude:

- Port-aux-Français: longitude 68.7194–71.7194, latitude −50.3492–−48.3492.
- Grytviken: longitude −38.0092–−35.0092, latitude −55.2811–−53.2811.

These are roughly two-hundred-kilometre windows, not claimed administrative
boundaries or complete sovereign/archipelago coverage. Their bounds are copied
exactly into both the osmium polygon and runtime coverage. A geometry gate proves
all corners are within the official source envelope and no source outer/hole
boundary crosses or lies inside a window. All 122 shared/window source rows
(including the original 19 residual rows) are assigned exactly once and lie within their declared graph coverage. Actual road availability
and routing still require native build gates and production acceptance.

## Source locks, clipping and publication

`tools/gap_inputs.py` regenerates the three reviewed input files from the frozen
index, gap roster and root's authenticated provider HEAD/MD5 evidence. It verifies
the index SHA from the audit. This is the first generation stage; always run
`prepare_global_build.py` afterwards to coalesce the nine children, restore the
120/1/1 native scopes, and regenerate all six final deploy inputs together.
`check_global_build.py` rejects scope drift between these files. Each custom
roster row additionally locks its exact
coverage feature SHA; a generic official-index refresh cannot silently turn a
technical window into whole-continent coverage.

`tools/gap_source.py pin` reads the downloaded dated PBF, verifies its authenticated
provider size and MD5, computes the actual SHA256, and writes `source-lock.json`.
The actual source SHA is intentionally **not invented in advance**. `extract`
requires that lock and recomputes it before starting osmium. Changing bytes after
pinning aborts before extraction. The final immutable regional manifest and READY
retain source URL, size, MD5 and SHA, output SHA, exact extraction method and clip
geometry SHA; both release publication and runtime supply use the same provenance
gate in `regional_release.validate_snapshot`.

The [official osmium extract documentation](https://docs.osmcode.org/osmium/latest/osmium-extract.html)
describes `-p` GeoJSON polygons and `complete_ways`. The workflow uses that two-pass
strategy and subsequently runs `osmium check-refs` for way→node references.
Complete ways can retain nodes outside the window; this is expected and does not
expand the declared routing envelope. Complete relations are **not promised**.
The proof records `complete_relations: false`; actual Valhalla admin/tile building
and routing remain mandatory rather than being inferred from a successful clip.

`gap-tiles-build.yml` is explicit-dispatch only, takes a reviewed source SHA, and
runs at most two of these independent source builds concurrently. It:

1. Downloads the dated PBF and pins its actual source SHA.
2. Creates the exact technical extract, or reuses Central America unchanged.
3. Runs the existing pedestrian filter and pinned, concurrency-one Valhalla build.
4. Runs the full structural checker and actual native pedestrian routes for every
   one of the graph's assigned source coordinates (120/1/1).
5. Publishes only after all three graphs and their exact roster/provenance pass
   the shared READY gate. The production release pointer is never changed here.

`tests/native_scope_smoke.py` requires Valhalla 3.3.0, retains the 768 MiB process
limit and eight-second request deadline, and requires a positive route shorter
than 5 km. The final proof must contain every frozen source-row ID exactly
once and match its coverage-bound probe SHA. The same gate is used for all
1,120 ordinary-addition rows, giving 1,242 required new-region route probes.
Each source center gets four nearby endpoint candidates. A native
crash/timeout fails immediately; a genuine no-edge/no-route can try the next
nearby endpoint. If none succeeds, publication fails and the specific coordinate
needs investigation. Center probes are public source data, not user coordinates.

A real osmium fixture in `test_gap_sources.py` checks that an inside way retains
its outside node and passes reference validation. It is skipped only where osmium
is absent locally; the workflow installs osmium and reruns that test suite before
any source download/build. Its full data and actual native gates have not run
until the workflow is dispatched successfully.

## Actions trigger review

`deploy-image.yml` watches pushes to `staging` and `codex/navigation-integrity`,
with `deploy/**`, `tests/**` and selected tool/workflow paths. It does not watch
`codex/global-navigation-r2`. Commit `548e3a6` contains matching `deploy/**` and
`tests/**` changes and no skip marker, so a genuine matching staging push should
not be dismissed as a local path-filter mismatch. New dispatch-only workflow
files also need GitHub registration/default-branch availability; the existing
registered `deploy-image.yml` can accept an explicit source SHA. Remote event,
workflow-enable and token provenance evidence is needed to diagnose a missing
staging run. This review does not change trigger filters or bypass deployment
checks.
