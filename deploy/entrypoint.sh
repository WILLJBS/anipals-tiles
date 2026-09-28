#!/bin/sh
# Materialise tiles onto /data (persistent disk) and serve them. The runtime
# config ships in the repo (runtime/valhalla_config.json) with tile_extract
# already pointed at /data/valhalla.tar — the same path the build pipeline pins.
set -e

TILES=/data/valhalla.tar
CONFIG=${VALHALLA_CONFIG:-/data/valhalla_config.json}
THREADS=${VALHALLA_THREADS:-2}

if [ ! -f /data/valhalla_config.json ] && [ -f /app/runtime/valhalla_config.json ]; then
  cp /app/runtime/valhalla_config.json /data/valhalla_config.json
fi

if [ ! -f "$TILES" ] || [ "${FORCE_TILE_DOWNLOAD:-0}" = "1" ]; then
  echo "[anipals-entrypoint] downloading tiles from $TILE_RELEASE_URL"
  curl -sfL --retry 5 "$TILE_RELEASE_URL" -o "$TILES.part" && mv "$TILES.part" "$TILES"
  ls -lh "$TILES"
fi

exec valhalla_service "$CONFIG" "$THREADS"
