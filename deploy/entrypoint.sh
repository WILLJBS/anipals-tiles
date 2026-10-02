#!/bin/sh
set -eu
# Immutable installations and durable migration state replace RESET_TILES.
valhalla_build_config --mjolnir-tile-dir /data/regions > /tmp/anipals-config.json
exec python3 /usr/local/lib/anipals/regional_boot.py
