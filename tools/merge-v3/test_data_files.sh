#!/bin/sh
# Build the page from the real releases plus a synthetic res-custom release
# (a copy of a real sup renamed 32RESCUS.BIN and three .RCD data files), then
# run test_merge_v3.js against it.  Nothing outside a temp dir is written.
set -e
HERE=$(cd "$(dirname "$0")" && pwd); REL="$HERE/../../releases"
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
for d in "$REL"/fpsup-*-v*; do ln -s "$d" "$T/$(basename "$d")"; done
R="$T/fpsup-res-custom-v0.0.1test"; mkdir -p "$R/fpSup/RESCUS"
SRC=$(ls -d "$REL"/fpsup-lossless-v0.2.0test)
cp "$SRC/AutoRun.txt" "$R/"; cp -R "$SRC/fpSup/LOADER.BIN" "$SRC/fpSup/UI" "$R/fpSup/"
cp "$SRC/fpSup/10LOSS.BIN" "$R/fpSup/32RESCUS.BIN"
for f in OG3K S16 OG35K; do printf 'RCD1 %s' $f > "$R/fpSup/RESCUS/$f.RCD"; done
( cd "$R"; for f in AutoRun.txt fpSup/LOADER.BIN fpSup/UI/*.BIN fpSup/32RESCUS.BIN fpSup/RESCUS/*.RCD; do
    printf '%-24s %9sB  sha256=%s\n' "$f" "$(wc -c < "$f" | tr -d ' ')" "$(shasum -a 256 "$f" | cut -c1-64)"
  done ) > "$R/MANIFEST.txt"
python3 "$HERE/build_catalogue_v3.py" --releases "$T" --out "$T/index.html" >/dev/null
MERGE_INDEX="$T/index.html" MERGE_RELEASES="$T" node "$HERE/test_merge_v3.js"
