# Valhalla 3.3.0 native cache review — diagnostic evidence

**Status: hypothesis under test; not a confirmed diagnosis.** The candidate CI
binary reports 3.3.0 in approximately 101 ms, but an original complete Canada
locate process exits with SIGSEGV (-11) after approximately 2.8 seconds. The
regional structural walk passes; a local pyvalhalla 3.2 reader succeeded on the
same graph. These facts do not establish that the 3.3 binary or graph is generally
incompatible. Production has not been changed; the candidate publication gate
remains blocked pending native diagnosis.

This review read five files from the official **3.3.0 tag**. It did not edit
runtime configuration or execute an additional native request. Source copies
are local under `/tmp/anipals-valhalla330-cache/` for comparison/backtraces.

## Cache semantics established by source

[graphreader.cc, lines 436–477](https://github.com/valhalla/valhalla/blob/3.3.0/src/baldr/graphreader.cc#L436-L477)
selects a flat cache by default: `use_lru_mem_cache` and hard control both default
to false. The candidate overrides this to an LRU with a 64 MiB hard cap.

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

## Matrix and interpretation

The pending CI matrix compares:

| Tile-cache/config policy | Process address-space ceiling |
|---|---|
| Original generated config, regional root only | 4 GiB |
| Original generated config, regional root only | 768 MiB |
| Candidate overrides including hard LRU 64 MiB | 4 GiB |
| Candidate overrides including hard LRU 64 MiB | 768 MiB |

Capture bounded stderr, exit status, elapsed time and peak RSS. RSS and virtual
address-space use are different measures; the limit is `RLIMIT_AS`. The status
command does not open the same tiles and cannot establish route-memory safety.

If the original 768 MiB variant succeeds and the candidate 4 GiB variant crashes,
that excludes a simple 768 MiB address-space explanation and implicates the
**configuration overrides as a group**. It does not yet isolate the LRU switch:
the candidate also changes connectivity/actions and search reservations. Add an
adaptive fifth variant that keeps all candidate overrides but disables LRU, or
capture a native backtrace in the suspected reach loop. A comparison changing
only hard-vs-soft LRU can further distinguish eviction during a request from the
cache data structure itself. If all variants fail, investigate other common
inputs/binary behavior without presenting LRU as established root cause.

Even if disabling hard eviction resolves the crash, retain independent process
concurrency/deadline/address-space limits. Cache settings are not a substitute
for a proven whole-process memory bound. Native routes must pass before any
immutable image is eligible for production promotion.
