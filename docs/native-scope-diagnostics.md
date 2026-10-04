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


## Locate protocol correction and native gate (2026-10-04)

The real Venezuela diagnostic reported two correlated edges but zero parsed
projections for every probe. This was a parser failure, not evidence that no
projection existed. `tests/native_scope_geometry.py` had read `edge.projected`,
and its invented fixtures repeated the same mistake. The pinned
[Valhalla 3.3.0 serializer](https://github.com/valhalla/valhalla/blob/3.3.0/src/tyr/locate_serializer.cc#L74)
emits flat `correlated_lat` and `correlated_lon` in both verbose and concise
locate responses; `projected` is only its internal C++ member.

The shared parser and all positive fixtures now use that external protocol.
Old nested fields are a negative control, never a compatibility fallback.
Existing artifacts are preserved: their zero projection counts cannot be used
as absence or distance evidence. The independent decoded route-shape offsets
remain valid; the Lander start offset was 2,296.115 m, and three offsets returned
identical start/end shape points. This protocol fix does not require rebuilding
that graph or alter source centers, native search settings or acceptance gates.

`tests/native_locate_smoke.py` is copied with the shared geometry parser into
the candidate image and called by the existing Canada native promotion gate.
It sends actual `locate` requests through the pinned native engine for Toronto
and Montreal, reusing already downloaded and verified Canada bytes. Both
verbosity modes must return the exact input identity, nonempty edges with valid
flat correlation coordinates, and a matching positive count from the same
parser used by diagnostics. Only numeric summaries are printed. An unknown
response shape blocks image publication; offline mocks alone are insufficient.
Local tests verify the failure controls and wiring; a successful Linux native
CI run is still required before claiming the actual protocol gate passed.

Lesson: check external response fields against the pinned serializer and a
real native response before writing fixtures. Internal member names and mocks
that mirror the implementation do not establish a wire protocol.
