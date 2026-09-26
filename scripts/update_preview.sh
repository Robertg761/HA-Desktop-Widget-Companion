#!/usr/bin/env bash
# Rebuild the vendored widget preview bundle from an HA Desktop Widget release.
#
# The preview is the desktop app's real renderer packaged for an iframe
# (`npm run build:panel` in HA Desktop Widget). It is committed here so HACS installs need no
# build step. Usage:
#
#   scripts/update_preview.sh v3.11.0
#
# Requires git, Node.js 22+, and npm.
set -euo pipefail

tag="${1:?usage: scripts/update_preview.sh <desktop release tag>}"
repo_url="${DESKTOP_REPO_URL:-https://github.com/Robertg761/HA-Desktop-Widget}"
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
target="$root/custom_components/ha_desktop_widget/frontend/preview"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

git -c advice.detachedHead=false clone --quiet --depth 1 --branch "$tag" "$repo_url" "$work/desktop"
(
  cd "$work/desktop"
  ELECTRON_SKIP_BINARY_DOWNLOAD=1 npm ci --ignore-scripts --no-audit --no-fund
  node scripts/build-panel.cjs
)

rm -rf "$target"
mkdir -p "$(dirname "$target")"
cp -R "$work/desktop/dist-panel" "$target"
# Browsers that can run the preview all load the woff2 icon font; the legacy formats only add
# megabytes to every install.
find "$target/assets" -type f \( -name '*.eot' -o -name '*.ttf' -o -name '*.woff' \) -delete

echo "Vendored preview $(tr -d '\n ' < "$target/PANEL_VERSION.json")"
