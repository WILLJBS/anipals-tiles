# Finite global collector continuation

The manual full collector workflow has six sequential execution slots and one
final acceptance job. Every slot restores the same immutable full spec and its
existing R2 journal. The source roster, candidate policy, source budget and private
publication rule do not change. Six slots are bounded execution capacity, not a
guarantee that collection will finish within that capacity.

Preparation remains a separate operation. For an existing namespace, execution
requires the exact previous result descriptor through the resume_result input.
An arbitrary newest object is never selected. The predecessor must be an explicit
Deadline checkpoint from a completed failed workflow run, independently bound to
its actual GitHub job and log. This permits a reviewed new runner SHA to resume an
older runner's checkpoint while preserving the same collector-code/spec identities
and full budget lineage. It does not rerun preparation or reset prior spending.

Within one run, the next slot requires the immediately preceding stage's verified
result and successful actual GitHub job. The receipt records run ID, attempt,
reviewed source/workflow SHA, job key, stage index, predecessor descriptor and
starting progress. The actual job log must attest the same result descriptor.
Credentials follow only the private execution step; dependency installation,
third-party setup and offline tests do not receive R2 credential environment
variables. Permissions remain contents:read and actions:read.

Only failureType=Deadline permits continuation. OSError, transport-error classes,
budget exhaustion, wrong hashes or scope identity, concurrent ledger changes,
missing receipts, hard timeouts, unknown errors and cancellation stop. Existing
bounded per-request transport retries remain unchanged. A stage that adds neither
a durable range nor a completed file also stops; inherited seed data is excluded
from that progress comparison. Unsettled reservations stay charged, including
after a crash.

The first setup step establishes a monotonic deadline at 330 minutes. Dependency
setup and all restore/revalidation time consume it. The collector's active timer
uses the smaller of its existing 300-minute spec duration and the remaining
whole-job allowance, leaving 20 minutes before the 350-minute job limit for
receipt publication and validation. Expiry before collection has established a
new result produces no resumable proof and stops. A hard job timeout or failed
receipt upload likewise cannot silently advance the chain.

A checkpointed job succeeding means that its checkpoint is valid; it does not mean
the global collection is complete. The finalizer independently verifies the exact
receipts and actual GitHub stage logs, then requires a complete result, all expected
source-file checkpoints, the full exact 6,222-source scope set, no collection or
geometry errors, matching candidate counts, no outstanding reservations and the
unchanged live budget ledger. Later stages must be skipped after completion.
Exhausting all six stages fails final acceptance visibly.

No schedule, workflow_run trigger, actions:write permission, automatic new-run
dispatch or production candidate publication is introduced. Manual cancellation
prevents further stages and final acceptance.

## Validation

Run the offline collector suite:

    python3 -m unittest discover -s tests -p 'test_cloud*.py'

Tests exercise stage/run/job binding, actual GitHub proof rejection, safe signed
log redirects, old-run bootstrap, budget and live-ledger disagreement, missing
receipts, no progress, six-stage exhaustion, cancellation, exact final scope and
summary checks, and setup/restore time consuming the total deadline. The local
schema integration also uses the locked real wrapped roster and a verified pilot
summary structure; that fixture is not evidence of global cloud completion.

A manually restarted staged run cannot reuse a stale predecessor after its ledger
has advanced. Start a new reviewed execute dispatch with the exact last terminal
Deadline receipt instead. Failed-job reruns that mix predecessor run attempts
are deliberately rejected; neither mechanism may bypass the stage ancestry check.
