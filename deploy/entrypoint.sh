#!/bin/sh
# Materialise tiles onto /data (persistent disk) and serve them.
# Tile source: the LATEST release of WILLJBS/anipals-tiles carrying a READY
# asset. Assets are per-country tars split into <=1.9 GB parts — download all
# parts, concatenate per country, untar into /data/tiles (flat .gph tree) and
# serve the graph in tile_dir mode (no valhalla_build_extract anywhere: the
# merged world graph is ~31 GB, beyond any runner or a build step here).
#
# Deploy-window strategy (2026-09-30): Render kills a deploy whose process
# never binds its port. Downloading 31 GB up front takes 20-60 min — far past
# that window ("Timed Out" after ~5 min). So: bind a placeholder HTTP server
# FIRST (/status reports not-ready until the marker exists, /route 503), keep
# downloading in the background, then hand the port to the real service.
# The download loop is resumable per country (a finished tar is skipped), so
# even a killed run continues from where it stopped.
set -e

THREADS=${VALHALLA_THREADS:-2}
TILES_DIR=/data/tiles
MARKER=/data/tiles/.complete

mkdir -p "$TILES_DIR"

# placeholder server: binds 8002 immediately so the deploy never hits the
# port-scan timeout; replaced by valhalla_service once tiles are ready
PLACEHOLDER=/tmp/placeholder.py
cat > "$PLACEHOLDER" <<'PYEOF'
import http.server, json, os
class H(http.server.BaseHTTPRequestHandler):
    def _send(self, code, payload):
        body=json.dumps(payload).encode()
        self.send_response(code); self.send_header('content-type','application/json')
        self.send_header('content-length',str(len(body))); self.end_headers()
        self.wfile.write(body)
    def do_GET(self):
        if self.path=='/status':
            self._send(200, {'ready':False,'state':'downloading'})
        else:
            self._send(503, {'error':{'code':'NAVIGATION_UNAVAILABLE','retryable':True}})
    def log_message(self, *a): pass
http.server.HTTPServer(('0.0.0.0',8002),H).serve_forever()
PYEOF
python3 "$PLACEHOLDER" &
PLACEHOLDER_PID=$!

download_done=0
if [ -f "$MARKER" ]; then
  download_done=1
elif [ "${FORCE_TILE_DOWNLOAD:-0}" = "1" ]; then
  rm -f "$MARKER"
fi

if [ "$download_done" != "1" ]; then
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
  # Pre-resolve slug -> part URLs to a TSV file. The 61-country release JSON
  # is ~85 assets; inlining $RELEASE_JSON into the xargs child shell blew the
  # exec arg limit ("xargs: Argument list too long", 2026-09-30) — the child
  # now reads only its own URLs from /tmp/part-urls.txt (a few hundred bytes).
  echo "$RELEASE_JSON" | python3 -c '
import json,sys
r=json.load(sys.stdin)
for a in sorted(r["assets"], key=lambda a: a["name"]):
    if a["name"].endswith(".tar-00"):
        slug=a["name"][:-len(".tar-00")]
        for b in r["assets"]:
            if b["name"].startswith(slug+".tar-"):
                print(slug+"\t"+b["browser_download_url"])' > /tmp/part-urls.txt
  # download+extract countries in parallel (4 workers); parts re-joined via
  # append so no full-size tar ever sits on disk next to its parts. A country
  # whose tiles are already on disk (previous boot) is skipped — resumable
  # across Render's deploy window.
  cat /tmp/countries.txt | xargs -P 4 -I{} sh -c '
    slug="{}"
    if ls /data/tiles/"$slug"/*.gph >/dev/null 2>&1 || ls /data/tiles/*.gph >/dev/null 2>&1 && [ -f "/data/tiles/$slug.done" ]; then
      echo "[anipals-entrypoint] $slug already materialised"; exit 0
    fi
    echo "[anipals-entrypoint] $slug"
    : > "/data/tiles/$slug.tar"
    grep "^$slug	" /tmp/part-urls.txt | cut -f2 | while read -r url; do
      curl -sfL --retry 5 "$url" >> "/data/tiles/$slug.tar"
    done
    tar -xf "/data/tiles/$slug.tar" -C /data/tiles --strip-components=1
    rm -f "/data/tiles/$slug.tar"
    touch "/data/tiles/$slug.done"
  '
  COUNT=$(find "$TILES_DIR" -name '*.gph' | wc -l)
  echo "[anipals-entrypoint] extracted $COUNT tile files"
  [ "$COUNT" -gt 0 ] || { echo "[anipals-entrypoint] no tiles extracted"; exit 24; }
  touch "$MARKER"
fi

# The per-country tars carry a tiles/ prefix; if an older entrypoint version
# unpacked them without --strip-components the graph sits one level too deep
# (valhalla reads /data/tiles/0/... and finds nothing). Flatten in place —
# cheaper than re-downloading 31 GB.
if [ -d "$TILES_DIR/tiles" ] && [ ! -d "$TILES_DIR/0" ]; then
  echo "[anipals-entrypoint] flattening nested tiles/ directory"
  find "$TILES_DIR/tiles" -type d -exec chmod 755 {} + 2>/dev/null || true
  find "$TILES_DIR/tiles" -type f -exec chmod 644 {} + 2>/dev/null || true
  cp -R "$TILES_DIR/tiles/" "$TILES_DIR/" 2>/dev/null || true
  find "$TILES_DIR" -maxdepth 1 -name '*.gph' -o -maxdepth 2 -name '[0-9]*' -type d | grep -q . || true
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

kill "$PLACEHOLDER_PID" 2>/dev/null || true
exec valhalla_service /data/valhalla_config.json "$THREADS"
