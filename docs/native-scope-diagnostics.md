# Native scope diagnostics

The scope gate still requires pinned native ABI 3.3.0 and one route with
`0 < summary.length < 5` kilometres from each unchanged declared source center
using the existing four offsets. A 404 tries the next offset; other engine
errors abort. Only complete success creates `native-probes.json`. Neither the
HTTP status mapping nor public error messages change.

`native-scope-diagnostic.json` is a separate schema-2 diagnostic, including on
failed runs. Both scope tools use `tests/native_scope_records.py` to allowlist
source-row/offset indexes, graph/source hashes, ABI match, coverage membership,
finite route length, edge count, error status, numeric native error/exit codes
and fixed classifications. Raw native responses, projected/request coordinates,
exception text and stderr are not exported. Unknown ABI text is not echoed.
The diagnostic is explicitly not acceptance and cannot substitute for native
proofs or READY. Both build workflows preserve this file with `if: always()`.

The Engine previously parsed native error codes but discarded them while
mapping failures to its existing safe 404/503 exceptions. Optional internal
exception metadata now retains numeric codes without changing that mapping.
Smoke previously wrote nothing until all scopes passed; its independent
failure artifact now preserves attempted outcomes without publishing success.
A hard-killed runner can still lose its diagnostic; missing evidence is not a
successful or negative route result. No live route evidence is claimed by the
local mock/native-child regression suite.

## Reusing the 127 successful regional graphs

This is a recovery plan, not an implemented recovery workflow. Keep existing
successful draft assets and manifests byte-for-byte. Verify their pinned image,
coverage feature, scope/source-row proof, graph inventory and every contiguous
part's size/SHA through the existing shared release gate. Resolve the two failed
regions using actual native evidence; do not move centers or lower thresholds.
Only then assemble the exact complete roster and call `release_manifest.ready`.
Conflicting assets must fail, never be overwritten to force a match. Changed
scope contracts need new proof for each affected region. A partial draft remains
unpublishable; diagnostics or representative graph-node health are not city
navigation acceptance. No release-rescue implementation is included here.

Raw PBF retention and real entrance evidence require separate acceptance.
Rebuilding mutable latest data cannot claim to reproduce a lost historical
graph. Product distance gates remain unchanged.
