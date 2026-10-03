# anipals-tiles

AniPals pedestrian navigation uses **61 isolated regional graphs**, selected by
exact official Geofabrik coverage polygons. Independent extracts reuse GraphIds;
they must never be overlaid. The regional runtime, bounded native process model,
activation and legacy migration are described in
[regional-runtime.md](docs/regional-runtime.md). [Linux CI](https://github.com/WILLJBS/anipals-tiles/actions/runs/36986181555)
for `c68d169` passed 49 tests and real Valhalla 3.3.0 Canada routes. Production
pins that tested image, including graph leases, retired-graph GC and catalog
activation-race handling. After the 2026-10-02 08:54 UTC deployment restart,
completed graphs were reverified without RESET or re-downloading. All **61/61
regions** completed migration with no observed native failure. The 163-city scan
and follow-up repairs verified routes in all **149 covered cities**: 148 registered
centers plus a reviewed Dubai park (its center fails the game place-distance gate).
The **14 existing coverage gaps** remain separate. API `fa38cfd` is live and
web/play at that commit are READY. The 2026-10-03 closeout check confirmed the
same live image, API readiness and a real Toronto route.

The current legacy release is approximately 87.46 GB. The previous 31 GB estimate
and shared tile-directory runtime are obsolete. Existing archive bytes can be
reused only as independently validated regional roots. See the
[root-cause evidence](docs/integrity-review-2026-10-02.md) and
[release/coverage contract](docs/regional-release-contract.md).

This public repository contains the OpenStreetMap-derived graph supply and its
routing runtime. Data is © OpenStreetMap contributors, licensed ODbL. Public
Release assets allow the service to fetch graphs without application credentials.

- `.github/workflows/build.yml`: explicit dispatch only; per-region builds,
  structural validation, <=1.9 GB archive shards, then exact-roster schema 2 READY.
  A `countries` subset stays a diagnostic draft; use registered ids such as
  `north-america/canada asia/malaysia-singapore-brunei`.
- `.github/workflows/deploy-image.yml`: reviewed SHA or staging source changes;
  local tests and real native candidate checks precede SHA-tagged image publication.
  It never silently moves a production `latest` tag.
- `.github/workflows/publish-ready.yml`: explicit validated draft-release rescue.

Run local safety tests with
`PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v`.
