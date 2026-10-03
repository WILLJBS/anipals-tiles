# Private source archive verification

This manual workflow verifies private AniPals source archives already uploaded by
`anipals-app/tools/object-storage/archive.py --stage`. It neither builds navigation
graphs nor changes the navigation or display-map providers.

The motivation is observed local full-GET latency: moving source-byte verification
to an approved cloud runner avoids downloading every source back to the laptop.
It does not substitute HEAD, ETag or metadata for content integrity.

## Dispatch contract

Review and commit verifier code, then dispatch **that exact ref** with:

- `source_sha`: full 40-character reviewed commit; must match workflow `GITHUB_SHA`
  and checked-out HEAD before any credential-bearing step runs.
- `bucket`: authorized private bucket, shared by stage objects and final receipt.
- `manifest_key`, `manifest_sha`, `manifest_bytes`: exact descriptor emitted by
  local stage, under `archive/staging/sha256/<sha256>.json`.
- `total_bytes`: exact reviewed `plannedBytes`, counting source path aliases too.

The three existing R2 secrets (`R2_ENDPOINT_URL`, `R2_ACCESS_KEY_ID`,
`R2_SECRET_ACCESS_KEY`) are exposed only to the verification step. Setting secrets
or dispatching is a separate operator action; this implementation performs neither.
No GitHub artifact, release asset, public URL or source path output is emitted.
Runner logs show only counters and opaque final receipt key/SHA/bytes. Do not enable
shell tracing or dump the private stage/receipt into job summaries.

The stage must declare a complete uploaded selection but remain `complete:false`,
with a distinct stage schema and every source `uploaded:true, verified:false`.
The verifier enforces unique normalized relative paths, content-addressed source
keys, private classification, counts, bytes and exact stage integrity. It rejects
premature verified/objectRef fields and forged normal-receipt schemas.

## Complete-read proof

Each unique source object is GET-read through 1 MiB buffers; both actual streamed
length and SHA256 must match. Transport failures can restart a GET at most three
times; integrity mismatches are not retried or hidden. Aliases sharing the same
key/SHA/bytes reuse one proof, but keep separate path entries in the receipt.

Only once **all** entries pass is the existing importer-compatible
`anipals-private-archive-v1`, `complete:true`, `verified:true` receipt constructed.
Conditional PUT prevents receipt overwrite; a concurrent winner must itself pass
full GET SHA and length verification. Receipts remain at private
`archive/receipts/sha256/<digest>.json`, and failures never emit a success result.
A retry rechecks source bytes and reproduces the same deterministic receipt.

Retrieve the small receipt privately with its exact printed SHA/length and supply
it to the app's existing `--object-manifest`. The importer then matches local
paths and recalculated input hashes, so a successful limited pilot cannot prove
that unrelated or remaining sources are archived.

Offline verification:

```sh
python3 -m unittest discover -s tests -p 'test_private_archive.py'
```

Passing mocks does not prove real R2 credentials, bucket privacy, cloud throughput
or source publication. Record an approved cloud pilot before full execution; API
business gates, approvals, city identity repair and real route tests remain separate.


The verifier owns the complete retry loop: its SDK client makes one attempt,
and each full GET (including body streaming) or conditional receipt PUT has at
most three total attempts with two-second spacing. Only transport failures and
explicit HTTP 429/5xx are retried; permission errors, missing objects and content
mismatches fail immediately. Conditional-write conflicts require a complete
GET/SHA check. SDK error metadata uses the shared fail-closed classifier so a
transport exception with `response=None` preserves its failure. Offline real
SDK/HTTP tests assert exactly three requests for repeated 503 on both GET and
PUT; retry layers cannot multiply into nine requests.
