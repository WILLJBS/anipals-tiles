# AniPals Valhalla pedestrian routing service (ADR-PLAY-11).
# Wraps the official image with a startup step that materialises the per-
# country tile tars from the latest READY GitHub Release onto the attached
# disk, then serves the graph in tile_dir mode.
FROM valhalla/valhalla:run-latest

# entrypoint dependencies the run image does not ship: python3 (READY-marker
# check) and curl (release download). Without them the container died ~47 s
# after start on Render (2026-09-30).
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends \
      python3 curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY deploy/entrypoint.sh /usr/local/bin/anipals-entrypoint.sh
RUN chmod +x /usr/local/bin/anipals-entrypoint.sh

EXPOSE 8002
ENTRYPOINT ["/usr/local/bin/anipals-entrypoint.sh"]
