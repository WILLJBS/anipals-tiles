# OSM entrance evidence integration contract

This is the integration boundary for the app extractor
`tools/discovery-places/osm_entrance_evidence.py`. Tile workflows do not yet invoke
it. A city-center routing probe proves graph availability, not a reviewed place
entrance or a completed user-facing navigation route.

## Inputs required before enabling CI

The extractor consumes the complete canonical candidate record from a `places`
array, an original PBF, and a per-region text list of `osm:way/<id>` or
`osm:relation/<id>` identifiers. These IDs come from explicit OpenStreetMap source
record IDs; an Overture UUID cannot be converted into an OSM ID by guess.
Candidate SHA and byte count must remain identical to the imported artifact.
A regional subset changes that SHA and cannot silently replace the original
candidate artifact at publication.

The integration needs three immutable inputs:

1. A frozen candidate artifact retrievable by CI and verified by SHA/bytes, plus
   the exact extractor revision and dependency bundle. Credentials must not be
   embedded in an artifact URL, commit or workflow argument.
2. A per-graph canonical-ID roster bound to candidate SHA and exact coverage
   feature SHA. The city-source-row map is insufficient: collection windows and
   destination polygons can cross graph borders. Bbox overlap may nominate a
   region for extraction; it does not prove entrance containment. Retain
   unresolved and overlapping cases.
3. A raw PBF lock recording source URL, SHA/bytes and retrieval evidence. The
   ordinary build downloads `latest` and deletes it after filtering; its tile
   inventory does not replace the raw PBF lock. The gap build already records a
   fixed snapshot lock and clipped-output SHA.

The current global candidate output is an incremental checkpoint, not a frozen
complete input. Do not feed every global ID to every regional PBF and classify
the resulting missing objects as absent entrances.

## Extraction placement and graph identity

For an ordinary build, hash and extract from `raw.osm.pbf` before the pedestrian
filter deletes it. The filter does not retain all venue polygon relations and
entrance tags, so filtered graph input is unsuitable as the entrance source.

For a technical window, extract parent membership from `source.osm.pbf` after
snapshot pinning and before deletion. `complete_ways` clipping does not promise
complete relations. Bind both the parent PBF SHA and clipped graph-input SHA,
then check the selected entrance against the exact technical-window coverage.
Central America uses the unchanged source. Matching tile filenames never prove
that source evidence belongs to another independently generated graph.

Each evidence artifact needs a companion envelope with candidate SHA/bytes,
canonical-ID roster SHA, extractor revision, graph slug, coverage feature SHA,
raw PBF SHA/bytes, graph-input SHA/bytes, evidence SHA/bytes and explicit missing
or rejected IDs. After tile validation, bind the graph fingerprint to the same
envelope. A native failure does not destroy source evidence; source evidence
alone does not publish a destination or prove routability.

Archive original source bytes (or an explicitly reviewed immutable archive),
referenced OSM objects, evidence and their envelope through verified R2
publication. Hashes alone do not preserve bytes after a mutable `latest` URL
changes. Job artifacts can stage transfer, but are not the final R2 archive.
Archival completion requires full object read-back verification. R2 credentials
stay outside the public build repository and release artifacts.

## Acceptance before workflow activation

App revision `a02ed71` adds an attributed frozen official OSM source fixture.
Actual osmium 1.19.1 encodes it into PBF and the production extractor recovers
the explicit public outer entrance; all four tests passed independently without
skips. This library fixture proves membership, not an approved animal venue.
Before enabling extraction in every build, also verify:

- Exact canonical parent, outer way and public entrance recovery, including
  tags and complete referenced membership.
- Parent/way/node access, foot prohibitions and locks cannot be overridden by a
  permissive child; inner and nearby unlinked nodes are not entrances.
- Missing requested IDs stay explicit. Changed candidate or PBF SHA fails
  before extraction. Outside-coverage entrances are not assigned to the graph.
- Substituted graph slug, coverage SHA, clipped source or evidence fails the
  manifest gate; source objects and evidence survive verified R2 read-back.
- Reviewed publication binds the original candidate artifact, and actual native
  acceptance routes to the exact verified point through the serving graph.

The global collector has not yet produced immutable complete per-graph candidate
inputs. The real fixture above does not supply those inputs or the archival and
publication gates. Integration remains inactive until the actual inputs and
runner exist; an empty success artifact or skipped job must not imply entrance
coverage.
