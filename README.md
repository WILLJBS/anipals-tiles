# anipals-tiles

Valhalla pedestrian routing tiles for AniPals in-game walking navigation
(ADR-PLAY-11 in the private `anipals-app` repo).

This repo is public on purpose: the tiles are **OpenStreetMap-derived data
(ODbL license)** and contain no application code — hosting the build here gets
free Actions minutes and a public Release asset the Render service can pull
without credentials.

- Workflow: `.github/workflows/build.yml` — per-country matrix (6 parallel),
  each job tars its tiles, splits into <=1.9 GB parts and uploads them to a
  draft Release; a final job publishes the release with a READY asset once
  every country has uploaded (subset reruns: dispatch with a `countries`
  list, e.g. `north-america/us asia/malaysia-singapore-brunei`).
- There is no global merge: the merged world graph is ~31 GB of tiles — the
  Render service (deploy/) downloads the per-country tars from the latest
  READY release into a tile directory and serves it in tile_dir mode.
- Build cost numbers land in `calibration/` after every run.

Tiles built from © OpenStreetMap contributors, data licensed ODbL.
