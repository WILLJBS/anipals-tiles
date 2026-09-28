# anipals-tiles

Valhalla pedestrian routing tiles for AniPals in-game walking navigation
(ADR-PLAY-11 in the private `anipals-app` repo).

This repo is public on purpose: the tiles are **OpenStreetMap-derived data
(ODbL license)** and contain no application code — hosting the build here gets
free Actions minutes and a public Release asset the Render service can pull
without credentials.

- Workflow: `.github/workflows/build.yml` — dispatch with a Geofabrik region id
  (`europe/iceland`, `asia/china`, ...) or push a workflow change.
- Artifacts: GitHub Releases (`tiles-<region>-<date>-<run>/valhalla.tar`),
  optionally mirrored to R2 (`anipals-valhalla-tiles` bucket).
- Build cost numbers land in `calibration/<region>.md` after every run.

Tiles built from © OpenStreetMap contributors, data licensed ODbL.
