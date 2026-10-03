# Valhalla 3.3.0 native cache review — diagnostic evidence

**Status: native CI has isolated the Montreal crash to hard LRU eviction.**
[Linux CI run 36983580857](https://github.com/WILLJBS/anipals-tiles/actions/runs/36983580857)
for tiles commit `2799fcb` passed with the actual Valhalla 3.3.0 binary, 35 Linux
tests, the complete original Canada graph and both final native routes. No gdb
backtrace was captured: the configuration trigger is established, but the exact
failing instruction and the pointer-lifetime mechanism below remain unconfirmed.
The current production image is the subsequent `c68d169` build, verified by
[CI 36986181555](https://github.com/WILLJBS/anipals-tiles/actions/runs/36986181555) with 49 Linux tests and
native Canada routes. It includes GC leases and catalog race handling. After the
08:54 UTC restart, completed graphs reverified without RESET/re-download. All
61 regions subsequently installed with no observed native failure or extra router
restart through acceptance. Routes passed in all 149 covered cities: 148 registered
centers plus a reviewed Dubai park; 14 existing coverage gaps remain. Dubai's
original center correctly fails the game's reviewed-place distance gate.

The original failure was a complete-Canada locate exiting with SIGSEGV (-11)
after approximately 2.8 seconds, despite a passing structural walk and native
status reporting 3.3.0. Five files from the official **3.3.0 tag** were reviewed;
local source copies remain under `/tmp/anipals-valhalla330-cache/`.

## Cache semantics established by source

[graphreader.cc, lines 436–477](https://github.com/valhalla/valhalla/blob/3.3.0/src/baldr/graphreader.cc#L436-L477)
selects a flat cache by default: `use_lru_mem_cache` and hard control both default
to false. The failing candidate enabled LRU with a 64 MiB hard cap. The final
configuration disables LRU and hard eviction, retaining the flat cache's 64 MiB
**soft target**. This target is not a hard memory cap; each native process has a
separate 768 MiB `RLIMIT_AS` address-space limit.

[graphreader.cc, lines 321–330 and 339–348](https://github.com/valhalla/valhalla/blob/3.3.0/src/baldr/graphreader.cc#L321-L348)
shows that hard mode evicts older entries during **every insertion** requiring
space. Eviction removes the cache's tile reference. A single tile larger than
the limit causes a runtime exception, rather than an explicit segmentation fault.
The source does not count tile references retained by callers against the cache
budget; the 64 MiB setting therefore does not bound total native process memory.
[search.cc, lines 110–117](https://github.com/valhalla/valhalla/blob/3.3.0/src/loki/search.cc#L110-L117)
keeps tile references in candidates, which can survive cache eviction.

A read-only size census of the complete local Canada graph found 10,336 tiles,
none larger than 64 MiB; the largest is 55,698,704 bytes. This rules out the
single-oversized-tile branch for that exact inventory, but not aggregate eviction.
Toronto's neighboring local-level files `2/000/769/362.gph` and
`2/000/769/361.gph` together exceed 64 MiB. Cross-tile lookups can therefore evict
cache entries during a single locate/reach expansion. This is a size observation,
not proof that these exact two files are the pair evicted by the failing request.

## Specific pointer-lifetime risks

The strongest locate-relevant risk is the inbound reach loop:
[reach.cc, lines 123–139](https://github.com/valhalla/valhalla/blob/3.3.0/src/loki/reach.cc#L123-L139).
It obtains an edge span from the current tile, iterates edge references, and then
calls `GetGraphTile(edge.endnode(), tile)` **overwriting the same tile holder**
inside that loop. Subsequent operations read the previous edge and continue the
previous span. If that source tile was evicted from the hard LRU and has no other
live owner, replacing the holder can free the underlying bytes while the loop
still references them. The initial tile is retained separately at line 74;
that protection does not necessarily cover other tiles visited during expansion.

The locate search calls this reach machinery via
[search.cc, lines 442–450](https://github.com/valhalla/valhalla/blob/3.3.0/src/loki/search.cc#L442-L450).
This supplies a concrete path to investigate with a crash backtrace. It is still
necessary to show that the failing request reaches a cross-tile iteration with
no remaining owner; source inspection alone does not establish the actual stack.

Related helpers exhibit the same ownership-sensitive pattern:
[graphreader.h, lines 617–647](https://github.com/valhalla/valhalla/blob/3.3.0/valhalla/baldr/graphreader.h#L617-L647)
updates a tile holder and then reads fields from the original raw edge pointer.
By contrast,
[graphreader.cc, lines 635–655](https://github.com/valhalla/valhalla/blob/3.3.0/src/baldr/graphreader.cc#L635-L655)
retains a separate local tile owner in `GetOpposingEdgeId`; that specific method
should not be incorrectly cited as unconditionally losing the source tile.

## Completed matrix and interpretation

The same original graph bytes were tested under six configurations:

| Config policy | Address-space ceiling | Montreal | Toronto |
|---|---|---|---|
| Original generated config, regional root only | 4 GiB | exit 0; about 159 MB RSS | exit 0 |
| Original generated config, regional root only | 768 MiB | exit 0; about 159 MB RSS | exit 0 |
| Candidate hard LRU, 64 MiB | 4 GiB | SIGSEGV (-11); about 214 MB RSS / 233 MB VM | exit 0 |
| Candidate hard LRU, 64 MiB | 768 MiB | SIGSEGV (-11); about 214 MB RSS / 233 MB VM | exit 0 |
| Candidate with soft LRU instead of hard | 768 MiB | exit 0; about 163 ms | exit 0 |
| Candidate changing only `use_lru_mem_cache` to false | 768 MiB | exit 0; about 163 ms | exit 0 |

The two final comparisons retain the other candidate overrides and isolate hard
LRU eviction as the failing policy. Raising the address-space ceiling does not
repair the hard-LRU variant; both alternatives succeed under 768 MiB. This rules
out the 768 MiB ceiling as the explanation for this reproduced failure. RSS and
virtual address space remain different measurements; these observed samples do
not establish an upper bound for arbitrary routes.

Toronto succeeds in all six variants, so the earlier neighboring-tile size
observation is supporting risk evidence, not a reproduction of Toronto failure.
The successful soft-LRU result is consistent with the source-level lifetime
hypothesis, but does not identify the actual failing frame without a backtrace.

The current flat-cache build `c68d16915e266193539595923b1a186b61277c8e`
passed final native routes: Toronto **0.362 km in 175 ms**, Montreal **0.551 km in
191 ms**. Its exact tested and live image digest is
`sha256:3428cba734d6cca4f03ed9eb36e2c2fb70ad304ba58b1b70986b0875dae77ac0`.
Native concurrency, deadline and 768 MiB address-space limits remain in force;
64 MiB is only the flat-cache soft target. This image includes the later
lease-based old-graph GC and catalog activation-race fixes. The intermediate
`3588b1a` candidate was not promoted to production. Same-release restart
reverification is observed; production deletion after a new release is not
claimed. All-region migration and city-scan classification passed; uncovered
regions remain a separate coverage task.
