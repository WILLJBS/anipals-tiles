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

Before creating a new multipart upload, the tool verifies the pinned 127-byte
PMTiles v3 header through the same strict HTTP Range reader. The reader sends
an honest `AniPals-Archive/1.0 (+https://anipals.app)` User-Agent. An existing R2
object is still fully GET-verified without depending on upstream availability.

The tool validates each sequential 64 MiB HTTP Range: exact source after
redirect handling, status 206, Content-Range including fixed total size, exact
length, and identity content encoding. Three attempts, two seconds apart, apply
to source range failures. It streams parts directly to a private R2 multipart
upload with per-part Content-MD5, bounded part buffers and no full archive on
runner disk. Full source SHA must match before `CompleteMultipartUpload` with
`IfNoneMatch: *`. Existing or concurrent objects are never overwritten.

A complete receipt is written only after the complete ordered R2 byte stream
passes size and full SHA checks. `tools/display_readback.py` reads bounded
64 MiB ranges in 1 MiB chunks and pins every request to the initial HEAD ETag
with `IfMatch`. Each response must have status 206, exact Content-Range and
length, unchanged ETag and SHA metadata, and identity content encoding.
Transient transport failures and premature EOF retry the current range at most
three stream attempts, two seconds apart; identity/metadata/range mismatches
fail immediately. Every attempt starts from a copy of the last successful
digest, so failed bytes never count twice. The existing SDK may additionally
retry pre-response requests up to three times (nine requests maximum per
range); it cannot retry a consumed stream. A final conditional HEAD rechecks
identity and metadata before returning the full SHA receipt and actual ETag. A conditional completion race
verifies the winner and aborts the caller's unused multipart. Source errors,
SHA mismatch and graceful cancellation abort an unfinished upload. Abort errors
retain the primary exception and report `multipart_abort_failed`.

The job timeout is 350 minutes, below the hosted runner's six-hour limit. The
shared CI timeout policy explicitly registers this large archive job's ceiling;
ordinary jobs keep their 180-minute maximum. It is
not a throughput guarantee. There is intentionally no serialized SHA state.
A new verification process rechecks every byte from zero; within a process,
only a failed range repeats. Restarting after multipart completion reuses the
existing object without any source read or upload. An interrupted unfinished
source transfer restarts from byte zero. SIGKILL/runner disappearance may prevent cleanup: an operator
must list and review abandoned multipart uploads and explicitly abort the exact
matching upload. An incomplete checkpoint must never be treated as a completed
asset. Do not publish a registry merely because multipart completion succeeded;
remote readback and the app's schema, route-byte, glyph, CDN and rendering gates
must pass first.

## Evidence status

Local tests cover successful full source/readback SHA, malformed HTTP ranges,
truncation, changed source, full SHA mismatch, conditional-winner integrity and
abort/interruption handling. The first cloud attempt failed at source HTTP retrieval without completing
publication; the fixed reader has passed a real 127-byte source preflight.
The subsequent cloud run completed multipart publication, but full R2
readback failed as described below. The corrected bounded readback still needs
a successful cloud run. No Worker release or production switch is implied. Receipts are uploaded as workflow artifacts;
secrets remain scoped to the transfer step and are never written to receipts.


## Source-client rejection diagnosed 2026-10-04

Cloud run `37208856142`, job `111455712066`, failed in six seconds with
`HTTPError` and `multipart_abort_failed=false`. In that revision the only
urllib call in the transfer was source Range retrieval; R2 calls use botocore.
The old diagnostic omitted HTTP status and stage.

The unchanged September 27 source was then checked directly. Python's default
User-Agent received HTTP 403 with `error code: 1010` from Cloudflare, for both
HEAD and a 127-byte GET Range. With the identifying AniPals User-Agent, HEAD
returned 200 and the original 86,753,200,519-byte length; strict Range requests
returned 206, the exact requested Content-Range and the PMTiles v3 header.
The source was still present; neither URL, size, SHA nor R2 object identity
needed replacement. One identified request returned an unsuitable 200 and was
not accepted as Range evidence. The implemented reader still rejects this
response and preserves its original bounded retry and full SHA gates.

`python3 tools/display_archive.py --check-source` now performs only this small
source preflight, with no credentials or R2 access. A failed preflight during
apply cannot create a multipart upload. Error records include a fixed stage,
HTTP status where available, error class/recognized S3 code and abort outcome;
response URLs, raw HTTP bodies and arbitrary exception messages are excluded.
Readiness is not claimed until a subsequent cloud run completes all original
full-object source and R2 integrity checks.


## Interrupted full-object readback diagnosed 2026-10-04

Cloud run `37209881731` completed all 1,293 source parts and the full source SHA
check, then failed at `r2_verify` with `ResponseStreamingError`. A later R2 HEAD
found the completed 86,753,200,519-byte object and matching SHA metadata. HEAD
metadata alone does not prove stored-byte integrity and is never a completion
receipt. The old verifier held one unbounded GET open for the whole object and
had no stream retry; a late disconnect discarded its entire verification.

The corrected verifier retains the pinned source URL, expected full SHA and
completed object. Each fully consumed range advances a `complete: false`
progress event; retry events report only offsets, counts and attempt number.
It never logs exception details, credentials, signed URLs or source bytes.
Only the caller's final full-hash/identity success writes `complete: true`.
The workflow runs both display test modules before applying the transfer.

Offline regression includes a real botocore `StreamingBody` transport failure
converted to `ResponseStreamingError`, failures after an already verified
range, retry exhaustion and closure, truncated/oversized responses, changed
ETag or metadata, conditional 412, invalid range/status, same-size corruption,
final HEAD drift, and reusing an existing object without upstream access.
The SHA gate covers the entire original byte sequence rather than independent
range hashes or a metadata-only check.

Protocol reference: [S3 GetObject Range and If-Match](https://docs.aws.amazon.com/AmazonS3/latest/API/API_GetObject.html).
