#!/bin/sh
# Arguments come from validated release metadata; never accept a size-only hit.
set -eu
file=$1 url=$2 size=$3 digest=$4
valid() {
  [ -f "$file" ] && [ "$(wc -c < "$file" | tr -d ' ')" = "$size" ] &&
    [ "$(python3 -c 'import hashlib,sys; h=hashlib.sha256(); f=open(sys.argv[1],"rb"); [h.update(c) for c in iter(lambda:f.read(1048576),b"")]; print(h.hexdigest())' "$file")" = "$digest" ]
}
valid && exit 0
current=0
[ ! -f "$file" ] || current=$(wc -c < "$file" | tr -d ' ')
# A complete-but-corrupt or oversized file can never be repaired by Range.
[ "$current" -lt "$size" ] || rm -f "$file"
if ! curl -sfL -C - --retry 3 --retry-delay 2 "$url" -o "$file"; then
  # Unsupported/ignored Range or a 416: a bounded fresh retry, no poison loop.
  valid && exit 0
  rm -f "$file"
  curl -sfL --retry 3 --retry-delay 2 "$url" -o "$file" || exit 1
fi
if ! valid; then
  echo "part failed size/SHA256 validation: $file" >&2
  rm -f "$file"
  exit 1
fi
