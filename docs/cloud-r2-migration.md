# Existing regional graph migration on GitHub runners

The local download path is unsuitable for the complete 87 GB release. Use the
explicit `migrate-release-r2.yml` workflow to run the same tested streaming
migrator near the existing GitHub release assets. It does not rebuild graph
bytes, overlay independent graphs, alter production pointers, or write to the
separate source-archive namespace.

## Explicit inputs and credential setup

The workflow must be registered on the default branch and run at a reviewed
revision. Inputs are the exact 40-character `source_sha`, `release_tag`, authorized
`bucket`, `contract`, `mode` (`pilot` or `full`), and an exact `pilot_region` slug. Defaults
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

In `full` mode, the successful pilot supplies the exact registered regional matrix. At
most four regions run concurrently, retaining one authenticated shard and one
tile per runner. Each region rereads and revalidates source metadata, downloads
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
