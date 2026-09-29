# AniPals Valhalla pedestrian routing service (ADR-PLAY-11).
# Wraps the official image with a startup step that materialises the per-
# country tile tars from the latest READY GitHub Release onto the attached
# disk, then serves the graph in tile_dir mode.

COPY deploy/entrypoint.sh /usr/local/bin/anipals-entrypoint.sh
RUN chmod +x /usr/local/bin/anipals-entrypoint.sh

EXPOSE 8002
ENTRYPOINT ["/usr/local/bin/anipals-entrypoint.sh"]
