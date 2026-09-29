#!/bin/sh
# Materialise tiles onto /data (persistent disk) and serve them.
# Tile source: the LATEST release of WILLJBS/anipals-tiles carrying a READY
# asset. Assets are per-country tars split into <=1.9 GB parts — download all
# parts, concatenate per country, untar into /data/tiles (flat .gph tree) and
# serve the graph in tile_dir mode (no valhalla_build_extract anywhere: the
# merged world graph is ~31 GB, beyond any runner or a build step here).
set -e

THREADS=${VALHALLA_THREADS:-2}
TILES_DIR=/data/tiles
MARKER=/data/tiles/.complete

mkdir -p "$TILES_DIR"

if [ ! -f "$MARKER" ] || [ "${FORCE_TILE_DOWNLOAD:-0}" = "1" ]; then
  echo "[anipals-entrypoint] resolving latest READY release"
  RELEASE_JSON=$(curl -sfL --retry 5 "https://api.github.com/repos/WILLJBS/anipals-tiles/releases/latest")
  echo "$RELEASE_JSON" | python3 -c '
import json,sys
r=json.load(sys.stdin)
assets=[a for a in r["assets"] if a["name"]=="READY"]
sys.exit(23 if not assets else 0)
print(r["tag_name"])' || { echo "[anipals-entrypoint] latest release has no READY asset yet; aborting"; exit 23; }
  TAG=$(echo "$RELEASE_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["tag_name"])')
  echo "[anipals-entrypoint] fetching tiles from $TAG"
  echo "$RELEASE_JSON" | python3 -c '
import json,sys
r=json.load(sys.stdin)
for a in sorted(r["assets"], key=lambda a: a["name"]):
    if a["name"].endswith(".tar-00"):
        print(a["name"][: -len(".tar-00")])' > /tmp/countries.txt
  # download+extract countries in parallel (4 workers); parts re-joined via
  # append so no full-size tar ever sits on disk next to its parts
  cat /tmp/countries.txt | xargs -P 4 -I{} sh -c '
    slug="{}"
    echo "[anipals-entrypoint] $slug"
    : > "/data/tiles/$slug.tar"
    for url in $(echo "'"$RELEASE_JSON"'" | python3 -c "
import json,sys
r=json.load(sys.stdin)
for a in sorted(r[\"assets\"], key=lambda a: a[\"name\"]):
    if a[\"name\"].startswith(\"$slug.tar-\"):
        print(a[\"browser_download_url\"])"); do
      curl -sfL --retry 5 "$url" >> "/data/tiles/$slug.tar"
    done
    tar -xf "/data/tiles/$slug.tar" -C /data/tiles
    rm -f "/data/tiles/$slug.tar"
  '
  COUNT=$(find "$TILES_DIR" -name '*.gph' | wc -l)
  echo "[anipals-entrypoint] extracted $COUNT tile files"
  [ "$COUNT" -gt 0 ] || { echo "[anipals-entrypoint] no tiles extracted"; exit 24; }
  touch "$MARKER"
fi

# runtime config: tile_dir mode (tile_extract stays empty so valhalla reads
# the loose .gph tree instead of a packed tar)
if [ ! -f /data/valhalla_config.json ] || [ "${FORCE_CONFIG:-0}" = "1" ]; then
  valhalla_build_config --mjolnir-tile-dir "$TILES_DIR" --mjolnir-concurrency "$THREADS" \
    > /data/valhalla_config.json
  python3 - <<'PYEOF'
import json
c=json.load(open('/data/valhalla_config.json'))
c['mjolnir']['tile_extract']=''
json.dump(c, open('/data/valhalla_config.json','w'))
print('[anipals-entrypoint] config: tile_dir mode')
PYEOF
fi

exec valhalla_service /data/valhalla_config.json "$THREADS"
