#!/bin/sh
# Fetch the 13 upstream illustrations into .cache/illustrations.
# Artwork is non-commercial fan work by bananaeat / ZipZipPipe's design.
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
out="$root/.cache/illustrations"
mkdir -p "$out"

for id in morning afternoon cafe rain dusk night code detective spring festival autumn snow holiday; do
    dest="$out/$id.png"
    if [ -s "$dest" ]; then
        printf '%-10s cached\n' "$id"
        continue
    fi
    tmp="$dest.part"
    printf '%-10s fetching\n' "$id"
    curl -fsSL --retry 4 --retry-delay 1 --max-time 120 \
        -H 'Accept: application/vnd.github.raw' \
        "https://api.github.com/repos/bananaeat/xiaoke-look/contents/illustrations/$id.png" \
        -o "$tmp"
    mv "$tmp" "$dest"
done

printf 'done: %s\n' "$out"
