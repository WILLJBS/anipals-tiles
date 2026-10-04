# Regional storage ownership and explicit local rollback

The schema-1 composite remains an append-only remote catalog: it cannot replace
baked local regions. A schema-2 runtime index opts into one durable storage owner
per configured region. It is distinct from the offline
`anipals-navigation-catalog-v1` supply evidence; validating a catalog does not
publish or activate a runtime index.

## Runtime index contract

Schema 2 retains `image`, `local_coverage_sha256` and `remote_regions` from schema
1. Every remote row still requires exact manifest SHA/size, graph fingerprint,
feature and bounded native activation probes. It adds:

- `catalog_sha256`: exact SHA of the reviewed supply catalog used by the index
  assembler. The reader treats the pinned runtime index as its authority; the
  assembler must independently verify this catalog and its source proofs.
- `previous_index_sha256`: explicit `null` for initial adoption; the exact prior
  index SHA for a subsequent change. Existing region records enforce this
  compare-and-swap. Same-index restart is idempotent.
- `storage_ownership`: object keyed by the exact union of baseline and remote
  coverage slugs. Each value has exactly `selected` and `rollback`.
- `selected`: `{ "storage": "local" | "r2", "fingerprint": "<64 hex>" }`.
  An R2 fingerprint is the existing runtime generation hash of the canonical
  `{manifest_sha256, image}` object, not the object inventory fingerprint.
- `rollback`: `null` or `{ "storage": "local", "fingerprint": "<64 hex>" }`.
  Replacing a baked local region with R2 requires an explicitly pinned, complete
  local rollback generation already present on the target volume. An added
  remote region cannot claim a baseline local rollback.

A baseline region's remote replacement must preserve its exact baked feature
hash. Local selections must name unreplaced baseline regions. Duplicate remote
rows, missing owners, unknown owners, changed coverage, mismatched R2 generation
or missing rollback fail closed. The current reader requires at least one remote
row; a runtime index is not needed for an unchanged all-local installation.
The complete original61/gap3/additions129 source validators remain unchanged;
this schema does not turn 127/129 source assets into complete supply.

## One persisted fact for readers and writers

Each controlled region has `regions/<slug>/ownership.json`. This records index
SHA, selected identity, rollback identity, explicit mode (`selected` or
`rollback`) and current active pointer. The control record becomes authoritative
for that region; any legacy `active.json` is ignored rather than rewritten into
a second competing fact. Immutable graph `.complete.json` files do not gain
index-dependent fields.

The installer acquires region activation locks in sorted order, validates all
retained/local markers and prior-index comparisons, then writes the per-region
control records atomically. Failure during validation changes no ownership.
A process crash during the multi-region writes can leave a partial installation;
startup cannot expose the new router until installation resumes and finishes.
Same-index retries preserve already written records, including explicit rollback
mode. This is resumable per-region publication, not a claim of one filesystem
transaction across all regions.

The local materializer consults the same owner before downloading. Activation
rechecks ownership under the same lock, so a worker that started earlier cannot
overwrite a selected R2 generation. Remote activation also supplies its index
SHA; a stale remote worker cannot activate after a reviewed index change. PID1
waits for the router's matching `ownership_index` status before starting local
materialization when a remote index is configured.

Ownership installation is not an atomic zero-downtime cutover across regions.
Selecting R2 immediately makes the previous local pointer unavailable for new
requests until native candidate activation succeeds.

A candidate must still pass native verification before activation. Failed
verification leaves the previous pointer and retained bytes intact. Readers do
not silently serve a pointer that differs from the selected owner: until the
candidate activates, that region is unavailable. After R2 activation, object
outages remain errors; local fallback requires explicit rollback.

## Rollback, GC and restart

The GC reads the same authoritative pointer and protects the retained local
fingerprint. Retained entries may remain in the retirement journal, but do not
count as blocked lease work. Other retired graphs retain the existing native
file-descriptor lease protection, rename tombstone and crash recovery behavior.

An authorized operator can invoke the deployed helper with the reviewed volume,
slug, index SHA and retained local fingerprint:

```sh
python3 /usr/local/lib/anipals/regional_rollback.py \
  --data-root /data --slug '<exact slug>' \
  --index-sha256 '<reviewed index SHA>' \
  --local-fingerprint '<retained local generation>'
```

The helper verifies that exact candidate through the local native verification
endpoint, then atomically switches both mode and active pointer in the ownership
record. It journals the old active generation before switching. A crash before
the atomic write leaves the old owner active; GC checks the active pointer and
cannot delete it. In-flight requests retain leases on the old remote graph until
they finish. Restarting the same index preserves explicit rollback and skips
remote reactivation. Returning to R2 requires a reviewed new index whose
`previous_index_sha256` matches; removing the environment variable alone is not
a rollback and does not erase durable ownership.

A fresh installation without an index or ownership records follows the previous
local activation and GC behavior. Schema-1 remote additions remain compatible.
For an indexed runtime, `/status.complete` requires the exact configured active
set and in-process verification of every current fingerprint. That is configured
runtime readiness, not proof of all global cities, source scopes or route targets.

## Build inputs and verification

The shared state, ownership and rollback runtime helpers match production Dockerfile's existing
`COPY deploy/regional_*.py` input and the `deploy/**` image-workflow trigger. No
new dependency or separate COPY input is needed. A regression test asserts both
sides. There is currently no Docker ignore file restricting these inputs.

Offline tests reproduce the former stale-local overwrite and premature rollback
GC, then cover owner mismatch, stale index token, CAS, missing rollback preflight,
failed activation, explicit rollback proof, restart, interrupted publication,
shared activation locks and in-flight remote leases. These tests do not replace
isolated native R2 activation/rollback acceptance against the reviewed volume and
index, nor authorize production activation by themselves.
