# Existing regional graph migration on GitHub runners

The local download path is unsuitable for the complete 87 GB release. Use the
explicit `migrate-release-r2.yml` workflow to run the same tested streaming
migrator near the existing GitHub release assets. It does not rebuild graph
bytes, overlay independent graphs, alter production pointers, or write to the
separate source-archive namespace.

## Explicit inputs and credential setup

The workflow must be registered on the default branch and run at a reviewed
revision. Inputs are the exact 40-character `source_sha`, `release_tag`, authorized
`bucket`, `contract`, `mode` (`pilot` or `full`), an exact `pilot_region` slug,
and optional `region_subset` JSON. A blank subset retains the complete roster. Defaults
select the original release and Canada; another original region can be chosen.
Select `contract=original61` (default), `gap3`, or `additions129`. The release
must contain the complete registered roster and READY; a partial draft is
rejected. For `gap3`, select `pilot_region=central-america`. See
[migration contracts](migration-contracts.md) for the exact SHA bindings.

Before dispatch, an authorized operator must configure these three encrypted
repository secrets through GitHub Settings → Secrets and variables → Actions:

- `R2_ENDPOINT_URL`
- `R2_ACCESS_KEY_ID`
- `R2_SECRET_ACCESS_KEY`

Credential values are never workflow inputs, files or commit content. They are
injected only into the individual steps that access R2, not checkout or pip.
The bucket input must match the key's authorized destination. This document and
the workflow do not create or upload any secret. Python 3.12 and boto3 1.42.97
are explicit; the latter matches the locally exercised conditional PUT client.

## Pilot and complete migration

Every run begins with a real twenty-tile pilot. Source metadata includes every
paginated asset page; exact roster, READY, image and asset size/SHA gates run
before downloading tile parts. Authenticated metadata requests refuse redirects,
so the GitHub token cannot follow a redirected URL to another origin. Public
asset downloads receive no GitHub Authorization header.

The pilot validates the full regional header inventory, validates each uploaded
tile against its source/ABI/cross-references, and fully GET-verifies twenty R2
objects. Only then does it upload its receipt to private R2 and GET-verify that
receipt. In `pilot` mode no full migration jobs run.

In `full` mode, the successful pilot supplies the complete registered regional
matrix, or the explicitly selected recovery subset. At most four regions run
concurrently. Each runner retains one authenticated source
shard, one current extracted tile, and a bounded queue of independently owned
tile files (see the upload bounds below). Each region rereads and revalidates source metadata, downloads
the pilot receipt by immutable SHA/size, and checks its schema, bucket and count.
The original migrator then rechecks the exact source contract. A changed release
cannot borrow a pilot receipt from another source contract.

Full region receipts include schema, contract, bucket, slug, graph fingerprint,
manifest SHA/size, feature and tile count, matching the original migration tool's
output. Only valid matching receipts are retained. Both pilot and regional
receipts live under
`navigation/migration-receipts/SOURCE_SHA/RELEASE_TAG/KIND/RECEIPT_SHA.json`.
They are never uploaded as public Actions artifacts. Graph and manifest object
keys retain the existing immutable content-addressed layout.

Both CLI entrypoints print a structured failure code instead of raw exception
details. Logs contain no credential values, signed URLs or endpoint identifiers.
An interrupted run may leave verified unreferenced tiles. Retrying GET-verifies
and reuses them; it does not overwrite immutable keys. A full run is complete
only when every region in the selected contract and every private receipt succeeds. Production
activation and real target routes remain separate acceptance steps.

## Local source reuse evidence

The preserved US Northeast concatenated tar was checked against the original
release's two part boundaries: 1,992,294,400 and 710,461,440 bytes. Each segment's
SHA matched its trusted release digest. This permits local source reuse while
keeping the same per-part SHA gate. The available Canada directory is unpacked
graph data without its original tar; it cannot substitute for trusted archive
bytes. The local cached pilot is twenty tiles only and does not start a second
61-region migration alongside the cloud job.

## Bulk transport and verified pilot (2026-10-03)

The cached US Northeast pilot completed twenty actual conditional PUT/full GET
SHA verifications without publishing a graph manifest. The original archive
part digests, ABI checks and cross-tile inventory checks remained enabled.
This is storage pipeline evidence, not a completed 61-region migration or
production activation.

Navigation migration and its receipt helper use a separate bulk connection:
10-second connect and 90-second read timeout, at most three SDK attempts. The
online regional reader retains 2-second connect and 3-second read timeouts and
its existing actor deadline. Source download budgets are unchanged.

A previous diagnostic wrapper masked a transport error with AttributeError:
botocore transport exceptions can expose `response=None`, so chaining `.get`
is unsafe. A subsequent unwrapped run identified the BotoCoreError handling
branch; its precise underlying SDK subclass was not retained. Do not label
that historical error as a proven ReadTimeoutError. The complete pilot then
succeeded with the separate bulk budget. An offline real-SDK HTTP test also
proves a delayed response raises ReadTimeoutError under the short budget and
succeeds with the bulk budget.

All tile, display and private-receipt publishers read SDK metadata through
`storage_errors.error_details`. Missing or malformed metadata remains unknown
and fails closed; it never authorizes object creation or conflict reuse.
Only recognized protocol error codes may enter logs. A conflict still requires
complete object GET/SHA validation. Transport failure is never treated as 404.


## Bounded tile publication and progress

The two-pass source and structural checks remain mandatory. The producer walks
and validates tiles sequentially against the complete authenticated header
inventory, then transfers each validated file into a separate upload directory.
Eight worker threads publish independent objects. Running and waiting files
share a **512 MiB disk budget**, with at most sixteen outstanding files. The
producer blocks until a completed worker releases capacity; it cannot reuse a
worker's file as the tar reader's next `current.gph`.

Capacity checks reserve the next source part plus 640 MiB for the current tile
(maximum 512 MiB) and safety margin, plus the entire 512 MiB upload queue budget.
The queue budget is not an in-memory buffer: each GET verifier reads 1 MiB at a
time. Tar cleanup and upload cleanup own separate directories. On producer or
worker failure, queued tasks are canceled, running tasks finish before their
files are removed, and no regional manifest or receipt is published. Already
verified content-addressed tiles remain available for a later validated retry.

The S3 client is created before the workers and shared without mutating its
metadata or event hooks; this follows the
[Boto3 client thread-safety contract](https://docs.aws.amazon.com/boto3/latest/guide/clients.html#multithreading-or-multiprocessing-with-clients).
Publisher counters use a lock. Every existing object still requires complete
GET/size/SHA verification before reuse. Every new object still requires
conditional PUT and complete GET/size/SHA verification. Only after every tile
succeeds can the main thread publish the regional manifest. The change does not
raise workflow timeouts or relax any source, ABI, cross-reference or hash gate.

Structured events identify source part authentication, inventory count/bytes,
upload start/progress/completion and structure completion. Upload progress
reports completed count/bytes, pending count/disk bytes, peak bounds and elapsed
time. Publisher metrics count verify and PUT method calls (not individual SDK
retry attempts), successful verified/uploaded bytes, and cumulative operation
milliseconds across workers. These cumulative durations are not wall time.
Progress is emitted at phase boundaries and roughly five-second collection
intervals; source download and individual structural validation emit on return.

### Recovery and completion boundaries

A timed-out region has no completion receipt merely because some tile keys
exist. A retry rechecks the complete source and structure, fully GET-verifies
existing objects and uploads missing objects; it cannot replace the SHA gate
with a key listing. Do not cancel other regions that are still finishing.
A recovery run must use a fresh pilot bound to its reviewed source revision.
Previously successful regional receipts retain their original source revision
and contract. Never rewrite old receipts to make them appear produced by new
code. A combined completion ledger must verify each original receipt and its
source/graph/manifest identity, with exact coverage of the registered roster;
a successful single-region recovery alone is not a complete release migration.

For regions that still exceed the bounded job lifetime after concurrency is
measured, the next architecture is deterministic object shards derived from the
full authenticated inventory, bounded by both object count and bytes. Each shard
must retain complete cross-reference inventory checks and report its exact
verified subset. A final reducer may publish the regional manifest only after
proving disjoint, exact coverage and matching source/graph identities. This
sharding/reducer path is not implemented by the upload queue and must not be
claimed as completed recovery support.


### Explicit recovery matrix

`region_subset` may be an explicit nonempty JSON array of distinct exact slugs,
for example `["russia"]` for the registered original-release region. Unknown
slugs, duplicates, empty arrays and malformed JSON fail closed. All source
assets, READY and the complete registered roster are validated **before** this
filter is applied. The filter changes only the migration matrix; it does not
change the complete source contract or any receipt identity.

With the default `queue_lane=auto`, a nonempty subset uses the fixed
`migrate-release-tiles-to-r2-recovery` group; a blank subset retains
`migrate-release-tiles-to-r2`. Thus one
recovery cannot replace the pending complete-release run in GitHub's
one-running/one-pending concurrency slot. Operators must still select regions
whose old jobs have finished or failed, and avoid superseding another pending
recovery. Every recovery repeats the twenty-tile pilot at its new reviewed
source SHA and retains its new private receipt. Scope logs explicitly report
both validated and selected region counts and mark `subset`; there is no
full-roster completion marker. Old successful receipts are preserved under
their original SHA and contract; a recovery is not proof for unselected regions.

### Upgrade an unfinished queue without redoing completed regions

Explicit `queue_lane=primary` or `recovery` changes only placement in the two
existing groups. It does not change the four-region concurrency, three-hour
deadline, source validation, subset selection, byte verification, pilot
requirement, or receipt identity. `auto` preserves previous dispatch behavior.
Use this when an old matrix still runs an obsolete uploader while a useful
single-region recovery runs in the other queue. First archive exact job states
and completed receipts; cancel/reconcile superseded pending runs and the old
active matrix, then dispatch only unfinished slugs to its now-vacant primary
queue. Preserve any active recovery slug outside that subset. Requeue a displaced
source contract explicitly on the new verified revision; never silently lose it.
Previously uploaded objects remain immutable and require full GET/SHA checks
on reuse. Source tar input may be read again; no source or runtime pointer is
removed. A cancelled matrix is not success, and its successful regional jobs
retain their original receipts rather than inheriting the replacement identity.
