# Isolated native storage ownership acceptance candidate

This harness exercises a candidate on an independently prepared local volume.
The explicit runtime-isolated workflow mode now invokes it through the
[controlled source runner](native-runtime-runner.md). This complete isolated
workflow has not yet passed a real native/R2 run. Offline tests use explicitly
synthetic tiles and a mocked engine; they are regression tests, not cloud proof.

The existing read-only native gate verifies individual immutable graphs. It does
not exercise the schema-2 owner pointer, local retention, or rollback. This
candidate composes the existing assembler and runtime state machine to close that
test gap without introducing a second catalog/receipt validator.

## Required trusted runner integration

The controlled runner uses the exact source SHA in both the actual GitHub runner
and workflow contexts, builds that candidate image, and creates a unique private
volume beneath its actual `RUNNER_TEMP`. Source preparation fetches and
verifies the registered original release bytes, materializes real local graphs,
performs native validation, and records the actual inventory on that same volume.
A caller-supplied “isolated” label does not attest a nonproduction volume.

In the same trusted run, it creates the isolated assembly request and invokes
this harness. The newly generated index SHA is a candidate acceptance result for
review, not a previously reviewed production index. This requires no additional
user approval loop. Production still needs its exact reviewed index and actual
production-volume inventory.

The CLI checks the existing `native_r2_contract.execution` identity, clean exact
checkout, candidate revision, and canonical descendant of `RUNNER_TEMP`. Those
checks are additional guards, **not substitutes for the controlled source
preparation runner**. It accepts the existing registered native acceptance
workflow context. Its runtime-isolated mode preserves that actual execution
contract and invokes this CLI through the source preparation entry point.

Run inside the exact candidate diagnostic environment, with existing pinned SDK
and native binaries, after controlled volume preparation:

```sh
python tests/native_runtime_acceptance.py \
  --source-sha "$SOURCE_SHA" \
  --request "$ASSEMBLY_REQUEST" --request-sha "$ASSEMBLY_REQUEST_SHA" \
  --objects "$PRIVATE_OBJECT_MIRROR" --volume "$ISOLATED_VOLUME" \
  --probe "$PRIVATE_ROUTE_PROBE" --probe-sha "$PRIVATE_ROUTE_PROBE_SHA" \
  --output "$PRIVATE_RESULT"
```

Environment includes the existing runtime R2 reader configuration,
`ANIPALS_REVISION`, and genuine GitHub execution context. Values are never printed.
The trusted runner must supply a fresh volume, private immutable object mirror,
and exclusive output path; it must collect private output and clean its container
and volume even on failure. The harness itself closes engines, cache, bridge,
client, and temporary files. It does not upload any input, index, or result.

## What is checked

- The assembler revalidates the catalog SHA, original source request, complete
  selected source groups, receipts/manifests, exact registered coverage/image,
  and full actual local tile hashes. No partial source group is accepted.
- Only one selected baseline region with retained local rollback is accepted.
  Production mode and additions without local rollback are rejected.
- Local native verification precedes owner installation. Native version must be
  3.3.0. Existing cold/hot verification checks actual R2 GET attempts, exact
  native shapes, endpoint offsets, bounded latency, and zero hot source I/O.
- Remote activation requires native proof. Runtime route selection must use that
  remote fingerprint and return the same validated shape.
- A stale local activation is rejected. GC retains the local rollback graph.
- Explicit native-verified local rollback must return the same route. A stale
  remote activation is rejected. GC cannot delete the remote graph while its
  native lease is held; after release, it must collect that graph.
- A separate Python interpreter reinstalls the exact index, with all R2
  environment variables removed and no object bridge. It must preserve durable
  rollback mode and route through retained local bytes. The volume device/inode,
  index SHA, expected local fingerprint, and result are bound to the parent.
- The final private success result is written only after child restart success.
  An intermediate session result explicitly has `restart_verified=false`.

The existing ownership semantics remain: installing a remote owner temporarily
hides the old local pointer until remote activation. Failed candidate activation
therefore leaves that isolated route unavailable; it does not silently revert
or claim zero downtime. The retained local bytes remain available for explicit
rollback. This harness must never be pointed at production.

## Completion and remaining dependencies

A successful isolated result always records `production_activated=false`,
`production_supply_complete=false`, `scope_routes_verified=false`, and
`published=false`. It includes candidate index/request/catalog/probe SHA and
actual execution identity. Shapes and coordinates remain in private artifacts;
stdout contains only event/hash/production status.

The full 193-region production gate is unchanged. Even a Germany-only isolated
candidate still needs all 61 receipts in its registered original source group.
A partial migration cannot be relabelled as a complete original source.
Likewise 127/129 does not satisfy the additions group.

The controlled source runner now provides private request/result transport,
candidate Docker execution, explicit workflow selection, and cleanup. Remaining
acceptance requires complete source-group inputs and a successful actual run
against its exact committed runner SHA. The diagnostic files are consumed
through the exact read-only checkout mount; they are not production Docker COPY
inputs. See the runner document for its input and resource contracts.
