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

# One-shot clean reset (set RESET_TILES=1 in the service env for exactly one
# deploy): the first pull round predated the per-country .done markers, so
# orphaned partial tars + unmarked extractions filled the disk and a resumable
# retry cannot distinguish them. Wipe once, then the marked single pass fits
# (31 GB tiles + <=8 GB transient on the 50 GB disk).
if [ "${RESET_TILES:-0}" = "1" ]; then
  echo "[anipals-entrypoint] RESET_TILES=1 — wiping $TILES_DIR for a clean marked pass"
  rm -rf "$TILES_DIR"
  mkdir -p "$TILES_DIR"
fi
# stray transient tars from any killed run are always safe to drop
rm -f "$TILES_DIR"/*.tar

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
  # Stagger legs (PAUSE seconds, default 20): release-CDN abuse limits trip
  # after ~15-20 back-to-back large downloads, killing every remaining leg of
  # the boot. The pause trades minutes for per-boot yield. Exported so the
  # single-quoted child sees it.
  PAUSE=${VALHALLA_LEG_PAUSE:-20}
  export PAUSE
  cat /tmp/countries.txt | xargs -P 2 -I{} sh -c '
    sleep $((RANDOM % PAUSE))
    set -e
    slug="{}"
    if [ -f "/data/tiles/$slug.done" ]; then
      echo "[anipals-entrypoint] $slug already materialised"; exit 0
    fi
    echo "[anipals-entrypoint] $slug"
    # Per-part files + curl -C - resume: a killed big download keeps its bytes
    # and the next boot continues the same part instead of restarting from
    # zero (the NA legs died mid-transfer every boot under truncate-and-retry).
    grep "^$slug	" /tmp/part-urls.txt | cut -f2 > "/tmp/$slug.urls"
    n=0
    while read -r url; do
      f="/data/tiles/$slug.part-$n"
      curl -sfL -C - --retry 5 --retry-delay 10 "$url" -o "$f" || {
        code=$?
        echo "[anipals-entrypoint] $slug part $n FAILED curl_exit=$code size=$(wc -c < "$f" 2>/dev/null || echo 0) head=[$(head -c 120 "$f" 2>/dev/null | tr -d '\0' | tr '\n' ' ')]"
        exit 1; }
      n=$((n+1))
    done < "/tmp/$slug.urls"
    # A truncated/garbage tar (CDN rate-limit page, ENOSPC) must fail the leg
    # loudly: unconditional .done markers froze damage in place (06:00 pull).
    cat $(ls /data/tiles/$slug.part-* | sort) > "/data/tiles/$slug.tar"
    tar -tf "/data/tiles/$slug.tar" > /dev/null
    tar -xf "/data/tiles/$slug.tar" -C /data/tiles
    rm -f /data/tiles/$slug.part-* "/data/tiles/$slug.tar"
    touch "/data/tiles/$slug.done"
  ' || echo "[anipals-entrypoint] some legs failed — missing legs resume on next boot"
  COUNT=$(find "$TILES_DIR" -name '*.gph' | wc -l)
  DONE_N=$(find "$TILES_DIR" -maxdepth 1 -name '*.done' | wc -l)
  EXPECT=$(wc -l < /tmp/countries.txt | tr -d ' ')
  echo "[anipals-entrypoint] extracted $COUNT tile files; legs done $DONE_N/$EXPECT"
  [ "$COUNT" -gt 0 ] || { echo "[anipals-entrypoint] no tiles extracted"; exit 24; }
  if [ "$DONE_N" -ge "$EXPECT" ]; then
    touch "$MARKER"
  else
    echo "[anipals-entrypoint] partial coverage — serving what exists; missing legs resume on next boot"
  fi
fi

# The per-country tars carry a tiles/ prefix; if an older entrypoint version
# unpacked them without --strip-components the graph sits one level too deep
# (valhalla reads /data/tiles/0/... and finds nothing). Flatten in place —
# cheaper than re-downloading 31 GB.
if [ -d "$TILES_DIR/tiles" ]; then
  echo "[anipals-entrypoint] flattening nested tiles/ directory"
  find "$TILES_DIR/tiles" -type d -exec chmod 755 {} + 2>/dev/null || true
  find "$TILES_DIR/tiles" -type f -exec chmod 644 {} + 2>/dev/null || true
  # same-filesystem rename: instant, no double space (cp would blow the disk)
  for d in "$TILES_DIR/tiles"/*; do
    base=$(basename "$d")
    if [ -e "$TILES_DIR/$base" ]; then
      cp -R "$d/." "$TILES_DIR/$base/" || true
      rm -rf "$d"
    else
      mv "$d" "$TILES_DIR/$base"
    fi
  done
  rmdir "$TILES_DIR/tiles" 2>/dev/null || true
  echo "[anipals-entrypoint] flattened; gph count: $(find "$TILES_DIR" -name '*.gph' | wc -l)"
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
