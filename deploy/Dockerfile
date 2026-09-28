# AniPals Valhalla pedestrian routing service (ADR-PLAY-11).
# Wraps the official image with a startup step that materialises the tile
# extract from a public GitHub Release onto the attached disk, then serves it.
# TILE_RELEASE_URL points at the release asset built by .github/workflows/build.yml.
FROM valhalla/valhalla:run-latest

COPY entrypoint.sh /usr/local/bin/anipals-entrypoint.sh
RUN chmod +x /usr/local/bin/anipals-entrypoint.sh

EXPOSE 8002
ENTRYPOINT ["/usr/local/bin/anipals-entrypoint.sh"]
