# Private full-scope source collection

This command continues the original 6,222 GeoNames source scopes. It collects pending candidates only; successful collection does not approve places, publish navigation targets, or establish route success. Every scope retains the original collector's explicit completion/failure result. The unchanged reviewed app bundle controls classification, deduplication, geometry and source identity.

## Locked partial seed

The offline seed planner in the app repository (`tools/discovery-places/cloud_seed_plan.py`) emits an archive allowlist and seed manifest, with no upload. Current locked inventory: 1,358 unique objects / 376,908,142 bytes, including 8 complete source-file JSON checkpoints (151,587,011 bytes) and 672 range bodies (217,362,647 bytes). The remaining objects are range metadata, original transfer/diagnostic ledgers, range budget, progress, summary and seed manifest. These are **partial**: the global source index contains 80 files, and no city is declared complete by a cached range.

Seed installation recomputes every full-roster file configuration from the real index, registry and exact city windows. A completed file retains its original identity check in the unchanged collector. Ranges retain URL, offsets, full SHA and byte length; every restored body is GET/SHA verified before use. SQLite scratch state is rebuilt and is not an authoritative checkpoint.

## Full preparation and execution

1. Stage the exact seed allowlist privately, then run the private archive verifier. A stage manifest cannot substitute for `anipals-private-archive-v1`, `complete:true` and verified matching entries for every seed dependency.
2. Complete the actual cloud pilot. Preserve its opaque `privateResult` descriptor. An offline fixture, an uploaded success JSON, or a self-claimed execution field does not satisfy preparation.
3. Create a private content-addressed request with schema `anipals-global-collector-request-v1`, `seed` and `seedArchiveReceipt` descriptors, `pilotResults` containing the actual pilot's result descriptors, and `maxMinutes` from 1 through 300. Every descriptor is `{key,sha256,bytes}`. Do not fill absent proof with a placeholder success object.
4. Dispatch `collect-private-places-full.yml`, operation `prepare`, with the reviewed exact runner commit and exact request key/SHA/bytes. Preparation makes authenticated read-only GitHub API calls for the result's actual run attempt and successful `pilot` job, downloads that job's bounded log without forwarding the token to its signed storage URL, and checks the log's exact final result descriptor. It archives these responses privately before building the contract. There is no trust-uploaded-proof or skip-proof flag.
5. Review the returned private spec and budget. Dispatch operation `execute` with that exact spec descriptor and reviewed runner commit. Execution fetches GitHub proof again, rechecks the seed receipt, recalculates foreign budget lineage and requires exact contract equality before any source request. Resume uses the same spec; changing the spec cannot reset accounting. All collector workflows share one concurrency group.

The CLI is `python tools/cloud_collect_full.py prepare|execute --source-sha "$SOURCE_SHA" --input-key "$INPUT_KEY" --input-sha "$INPUT_SHA" --input-bytes "$INPUT_BYTES"`. It requires actual GitHub workflow environment identity; no local execute override exists. Workflow secrets are limited to the storage step. Source/code/checkpoints/results and raw logs are private R2 objects, never public Actions artifacts. Public output is counters and opaque descriptors only.

## Budget lineage and restarts

The original source budget is 78,981,406,971 bytes. Local collection charged 220,465,298 bytes and diagnostic probes charged 437,900 bytes. The often quoted 78,760,503,773 bytes is **before all cloud pilot charges**, not an available full-run allowance.

Preparation enumerates every matching collector namespace in private R2, verifies its immutable spec and journal, and includes its latest actual spend plus unresolved reservations at the full three-attempt upper bound. Unsuccessful/unproved pilots also count. A prior full namespace contributes only its increment above inherited prior; copied receipts cannot count a namespace twice. Missing prior accounting or ambiguous same-index/different-roster lineage fails closed. Any foreign journal drift after preparation requires replan. Replan merges full-scope file checkpoints and compatible range caches, preserving the budget rather than restarting it.

Source reservations persist before requests. A killed job retains its unresolved upper charge; resume does not credit it away. R2 GET/PUT bytes and dependency installation are separate from this source-provider budget. A failed multipart retry can retransmit parts; that R2 cost is not presented as source traffic. A run deadline emits a private partial result and retains resumable checkpoints. A hard runner kill can lose the final summary but cannot erase an already persisted reservation.

## Acceptance and remaining external gates

Offline real-data replay restored all 6,222 scopes, 8 file identities and 672 ranges, rehashed all 376,908,142 local bytes, kept 220,903,198 prior bytes charged, performed zero source requests and rejected changed file configuration/range identity. This proves the restore path, not cloud execution or new navigation coverage.

Live full dispatch remains blocked until the actual pilot is successful and all seed objects have a complete verified private archive receipt. Credentials authorization, actual pilot run, full collection, evidence-based place review and real route acceptance remain separately required. No fixture proof is shipped as production evidence.

The full job is explicitly registered with a 350-minute CI ceiling: up to 300 minutes for source collection and the remaining window for setup and private checkpoint publication. Ordinary jobs keep their existing 180-minute ceiling. Both the SDK preparation registry and the timeout policy include this new workflow.
