# Controlled isolated runtime acceptance runner

The explicit **runtime-isolated** mode of the existing native R2 acceptance
workflow prepares its own temporary local graph and exercises the schema-2
storage owner lifecycle. The default **read-only** mode keeps its existing
transport-only behavior. This wiring is a candidate until its exact committed
SHA passes CI and an actual complete-source private request is run successfully.
No real isolated runner execution is claimed by the offline tests.

## Input and provenance

The runner consumes one canonical, immutable private JSON request with schema
**anipals-isolated-runtime-request-v1**. It contains exactly the runner source SHA,
one baseline region slug, catalog/catalog-request/probe descriptors, the complete
explicit immutable metadata descriptor tree, and four resource budgets. Each
object descriptor has exactly key, SHA256 and byte length. Arbitrary URLs,
commands, volume paths and user-supplied isolation labels are not accepted.

Create the request offline with the shared catalog verifier:

~~~sh
python tools/plan_native_runtime.py \
  --source-sha "$SOURCE_SHA" --slug "$REGION_SLUG" \
  --catalog-ref "$CATALOG_DESCRIPTOR_FILE" \
  --catalog-request-ref "$CATALOG_REQUEST_DESCRIPTOR_FILE" \
  --probe-ref "$PROBE_DESCRIPTOR_FILE" --objects "$IMMUTABLE_LOCAL_MIRROR" \
  --metadata-bytes "$METADATA_BUDGET" \
  --source-download-bytes "$SOURCE_BUDGET" \
  --archive-bytes "$ARCHIVE_BUDGET" --output-bytes "$OUTPUT_BUDGET" \
  --output "$PRIVATE_CANONICAL_REQUEST"
~~~

Descriptor files are bounded JSON; the mirror uses SHA filenames and is verified
locally. The planner observes the exact reads of the existing catalog verifier
to derive and deduplicate the complete tree. It does not upload or activate
anything. The resulting request is saved with mode 0600 and no overwrite.

Dispatch the existing workflow at that exact source commit with mode
**runtime-isolated**, the authorized bucket, and the private request's exact
key/SHA/byte length. The runner checks actual GitHub run/workflow SHA, workflow
path, repository, ref and attempt; the mounted checkout is clean and the candidate
image revision matches. The planner/runner do not mint alternative receipt
contracts or relabel earlier runner SHAs.

Complete registered source groups remain mandatory. Selecting Germany still
requires the whole original 61-region source group and all its valid receipts.
An incomplete original group or 127/129 additions is rejected before downloading
the selected source archive. Other registered groups must be explicitly deferred
or completely supplied. A single-region candidate never proves the global
193-region production scope complete.

## Owned volume and execution

The trusted host runner creates a unique directory under its actual RUNNER_TEMP.
It mounts the exact checkout and request read-only and only its newly created
runtime directory read-write. The container runs as the host runner UID/GID;
there is no production volume input, privileged container, host data mount,
service deployment, or cloud resource creation.

1. Fetch declared private metadata once, verify complete SHA/size and exact
   namespaces using the shared catalog planner, and reject unused/undeclared tree
   objects. Repeated reads verify the local copy and never refetch a corrupt copy.
2. Resolve the one original baseline region from the registered release/READY.
   Verify official release URLs and part digests with the shared supply contract.
3. Download that region's original parts under a shared conservative byte budget.
   The shared materializer rechecks full part SHA, performs safe tar extraction
   and complete tile structure validation, then writes its actual graph identity.
4. Execute native verification on the local graph, activate only that isolated
   local pointer, and capture actual tile hashes, marker, image and device/inode.
5. Build an isolated assembly request for that same volume. Reuse the full
   catalog assembler and its exact inventory/manifest comparison. The resulting
   index is a newly generated candidate, never a preapproved production index.
6. Call the frozen native runtime harness: real R2 cold/hot route endpoints,
   remote activation, active route selection, stale worker rejection, retained
   local rollback, leased GC, and a separate credential-free local restart.
7. Verify private output identities and cross-links before uploading evidence.
   Container cleanup runs even after execution timeout. The host temporary
   directory is removed on exit, including failed acceptance.

The image build uses the existing production candidate and generic diagnostic
SDK Dockerfile. New runner/harness Python files are consumed through the exact
read-only checkout mount, not copied into production. Therefore production COPY
inputs remain unchanged. The workflow and runner explicitly identify the
checkout, diagnostic Dockerfile and mounts as their build/execution inputs.

## Resource and failure bounds

| Budget | Meaning | Allowed upper bound |
| --- | --- | --- |
| metadata_bytes | Sum of unique declared metadata object lengths | 2 GiB |
| source_download_bytes | Conservative shared charge for original parts and runtime R2 tile bodies, including failed attempts | 64 GiB |
| archive_bytes | Total selected uncompressed tar parts; shared extraction also limits expanded tile bytes to this total | 8 GiB |
| output_bytes | Total serialized private output uploads; candidate bundle uses at most half | 64 KiB to 8 MiB |

Private request size is independently capped at 2 MiB. Metadata objects have
per-kind bounds (probe 8 KiB, request 2 MiB, READY 4 MiB, receipt 8 MiB,
release 16 MiB, manifest/catalog 32 MiB). The existing private fetch uses at most
three whole-object attempts with one SDK attempt each. Metadata budget denotes
unique logical bytes; its transport upper bound includes those bounded retries.

Source archive streaming uses at most three attempts, a 30-second read timeout,
1 MiB chunks, exact size/SHA, and a reservation before every attempt. Runtime R2
reads use the existing reader and reserve three times the manifest tile size plus
one 1 MiB final-chunk allowance before I/O. A single lock protects reservations
and actual byte accounting across concurrent bridge handlers. Failed reservations
or transfers do not refund spent budget. Cross-graph and oversized bodies fail;
streams close on early termination. Private metadata and output readback are
separately bounded and are not charged as original/tile source bytes.

Before fetching metadata, free space must cover its budget plus 1 GiB. Before
materialization, free space must cover twice the selected archive size plus
1 GiB, in addition to already present metadata. The native cache is limited to
512 MiB **of disk**, native workers to 768 MiB, and the diagnostic container to
1536 MiB/2 CPUs. Source hashing and downloads stream rather than loading tiles
into memory.

The isolated job is capped at 60 minutes. Candidate build, diagnostic build and
container execution have separate 600/300/2100-second bounds, followed by a
30-second forced container cleanup. The step is capped at 55 minutes. A timeout
is failure, not partial acceptance. The read-only job remains at 25 minutes.
The modes use distinct fixed concurrency groups with cancellation disabled.

## Private result semantics

All logs and artifacts use the private isolated-acceptance namespace. No Actions
artifact or production runtime-index namespace is written. The result contains
content-addressed descriptors for the assembly request, actual inventory,
candidate index, coverage, native session and source preparation proof, plus a
bounded private log. Stdout contains only the result descriptor and completion
booleans; it does not disclose coordinates, shapes, source URLs or credentials.

The result gate verifies catalog/request/probe/index/inventory SHA links,
coverage canonical SHA, the same target identity across assembly/inventory/index,
local native proof and source fingerprint, selected remote identity, source byte
accounting, and independent-process rollback success. A hash-consistent but
semantically inconsistent artifact tree is rejected.

Complete means **this isolated session** passed. ProductionActivated,
productionSupplyComplete and published remain false; broad scope route completion
and index activation declarations also remain false. Upload failures or wrong
artifacts never produce a successful result. Incomplete private artifacts may
remain addressable after an interrupted upload, but no completion marker is
written and no production owner is changed.

Actual execution still depends on complete source-group receipts and an exact
reviewed runner commit. Offline tests cover synthetic extraction, validation
ordering, budget exhaustion/concurrency, stream failures, metadata drift,
artifact substitution, and timeout cleanup; they do not establish real R2/native
or global route coverage success.
