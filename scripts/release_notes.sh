#!/usr/bin/env bash
# Print the CHANGELOG.md section for one version, without its heading.
#
#   scripts/release_notes.sh 0.3.0
#
# Exits non-zero when the version has no section or the section is empty.
set -euo pipefail

version="${1:?usage: scripts/release_notes.sh <version>}"
changelog="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/CHANGELOG.md"

notes="$(awk -v heading="## [${version}]" '
  index($0, heading) == 1 { found = 1; next }
  found && /^## \[/ { exit }
  found { print }
' "$changelog")"

# Drop leading and trailing blank lines.
notes="$(printf '%s\n' "$notes" | sed -e '/./,$!d' | sed -e ':a' -e '/^\n*$/{$d;N;ba' -e '}')"
if [ -z "$notes" ]; then
  echo "CHANGELOG.md has no notes for ${version}" >&2
  exit 1
fi
printf '%s\n' "$notes"
