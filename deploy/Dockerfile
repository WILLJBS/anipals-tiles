# AniPals Valhalla pedestrian routing service (ADR-PLAY-11).
# Independent immutable region roots, official extract polygons, bounded native
# one-shot requests and resumable migration. The pinned candidate is tested on
# original Canada release bytes before its immutable SHA image is published.
ARG VALHALLA_IMAGE
FROM ${VALHALLA_IMAGE}
ARG REVISION=local
ENV ANIPALS_REVISION=${REVISION}

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
COPY deploy/regional_*.py tools/validate_tiles.py /usr/local/lib/anipals/
COPY tests/native_smoke.py /usr/local/lib/anipals/native_smoke.py
COPY tests/native_diagnose.py /usr/local/lib/anipals/native_diagnose.py
COPY deploy/coverage.json /usr/local/share/anipals-coverage.json
COPY deploy/probes.json /usr/local/share/anipals-probes.json
COPY deploy/tile-release.txt /usr/local/share/anipals-tile-release.txt
RUN chmod +x /usr/local/bin/anipals-entrypoint.sh /usr/local/bin/download-part.sh

EXPOSE 8002
ENTRYPOINT ["/usr/local/bin/anipals-entrypoint.sh"]
