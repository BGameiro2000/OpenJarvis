#!/bin/sh
# Apply deploy/unraid/patches/*.patch to an upstream checkout (default: cwd).
# A patch upstream already contains is reported and skipped; one that no
# longer applies fails the build so it gets looked at.
set -eu
root="${1:-.}"
dir="$(cd "$(dirname "$0")" && pwd)"
for p in "$dir"/*.patch; do
    [ -e "$p" ] || continue
    name="$(basename "$p")"
    if git -C "$root" apply --check "$p" 2>/dev/null; then
        git -C "$root" apply "$p"
        echo "applied $name"
    elif git -C "$root" apply --reverse --check "$p" 2>/dev/null; then
        echo "skipped $name: upstream already has it; delete the patch"
    else
        echo "ERROR: $name no longer applies to this upstream commit" >&2
        exit 1
    fi
done
