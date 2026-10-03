# Official display archive transfer

The only implementation of the large display archive transfer is
`tools/display_archive.py` in this repository. Its manual workflow is
`.github/workflows/display-basemap-publish.yml`; it shares the three existing
R2 secrets with navigation migration. An exact reviewed 40-character commit SHA
and the authorized bucket are required dispatch inputs. This job does not
change navigation graphs, the application, Worker registry, public access, or
production configuration. Display API/style/font contracts remain in the app
repository's `services/basemap-worker/README.md` and `docs/display-basemap-r2.md`.

## Pinned supply and rights

- Source: `https://btrfs.openfreemap.com/areas/planet/20260927_080001_pt/tiles.pmtiles`
- Size: 86,753,200,519 bytes.
- Official full SHA-256: `bae742bf7a931eb598e3a8c8dd9f287c3bc6bfc4b380ed0f3c614aba06b8ecd6`.
- Format: PMTiles v3, gzip MVT, OpenMapTiles schema, zoom 0–14. The app's existing
  brand style and required layers/fields must still pass actual metadata checks.
- Preserve OpenFreeMap source provenance, OpenMapTiles attribution and
  OpenStreetMap contributor/ODbL notices. Publication rights/attribution remain
  release inputs; a successful byte transfer is not a license approval.
- The app font publisher separately retains the official Noto Regular/Bold
  glyph bytes and both supplied OFL `LICENSE.md` files. No synthesized map or
  font resources are introduced by this transfer.

## Transfer and completion

The tool validates each sequential 64 MiB HTTP Range: exact source after
redirect handling, status 206, Content-Range including fixed total size, exact
length, and identity content encoding. Three attempts, two seconds apart, apply
to source range failures. It streams parts directly to a private R2 multipart
upload with per-part Content-MD5, bounded part buffers and no full archive on
runner disk. Full source SHA must match before `CompleteMultipartUpload` with
`IfNoneMatch: *`. Existing or concurrent objects are never overwritten.

A complete receipt is written only after a full R2 GET validates size, SHA and
SHA custom metadata and records the actual ETag. A conditional completion race
verifies the winner and aborts the caller's unused multipart. Source errors,
SHA mismatch and graceful cancellation abort an unfinished upload. Abort errors
retain the primary exception and report `multipart_abort_failed`.

The job timeout is 350 minutes, below the hosted runner's six-hour limit. The
shared CI timeout policy explicitly registers this large archive job's ceiling;
ordinary jobs keep their 180-minute maximum. It is
not a throughput guarantee. There is intentionally no serialized SHA state or
complex partial-transfer resumption; an interrupted unfinished transfer restarts
from byte zero. SIGKILL/runner disappearance may prevent cleanup: an operator
must list and review abandoned multipart uploads and explicitly abort the exact
matching upload. An incomplete checkpoint must never be treated as a completed
asset. Do not publish a registry merely because multipart completion succeeded;
remote readback and the app's schema, route-byte, glyph, CDN and rendering gates
must pass first.

## Evidence status

Local tests cover successful full source/readback SHA, malformed HTTP ranges,
truncation, changed source, full SHA mismatch, conditional-winner integrity and
abort/interruption handling. The cloud workflow and real R2 conditional multipart
completion have not run as part of this local implementation. No Worker release
or production switch is implied. Receipts are uploaded as workflow artifacts;
secrets remain scoped to the transfer step and are never written to receipts.
