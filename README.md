# anipals-tiles

AniPals pedestrian navigation uses **61 isolated regional graphs**, selected by
exact official Geofabrik coverage polygons. Independent extracts reuse GraphIds;
they must never be overlaid. The regional runtime, bounded native process model,
activation and legacy migration are described in
[regional-runtime.md](docs/regional-runtime.md). [Linux CI](https://github.com/WILLJBS/anipals-tiles/actions/runs/36983580857) passed
35 tests and real Valhalla 3.3.0 Canada routes with the final flat-cache policy.
Production now pins the verified image digest; Canada and US South are confirmed
installed. API `68fa5da` is live and web/play `68fa` is READY. Migration of all 61
regions and the 163-city scan remain incomplete. The new lease-based retired-graph
GC candidate `3588b1a` passes 46 local tests and awaits native CI/deployment;
it is not part of the live image.

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
