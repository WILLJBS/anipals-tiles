# AniPals Valhalla pedestrian routing service (ADR-PLAY-11).
# Wraps the official image with a startup step that materialises the per-
# country tile tars from the latest READY GitHub Release onto the attached
# disk, then serves the graph in tile_dir mode.
# Pinned by digest (was :run-latest): the serving image MUST stay byte-identical
# to the one that BUILT the tiles — run-latest moved between the 2026-09-30
# tile build and the 2026-10-01 image rebuild, and digest equality was the
# only way to rule version drift in/out during the tile-corruption triage.
# Bump deliberately, together with a full tile rebuild.
ARG VALHALLA_IMAGE
FROM ${VALHALLA_IMAGE}

# entrypoint dependencies the run image does not ship: python3 (READY-marker
# check) and curl (release download). Without them the container died ~47 s
# after start on Render (2026-09-30).
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends \
      python3 curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY deploy/download-part.sh deploy/release-plan.py deploy/install-tiles.py /usr/local/bin/
COPY deploy/regions.json /usr/local/share/anipals-regions.json
COPY deploy/valhalla-image.txt /usr/local/share/anipals-valhalla-image.txt
COPY deploy/entrypoint.sh /usr/local/bin/anipals-entrypoint.sh
RUN chmod +x /usr/local/bin/anipals-entrypoint.sh /usr/local/bin/download-part.sh

EXPOSE 8002
ENTRYPOINT ["/usr/local/bin/anipals-entrypoint.sh"]
