#!/usr/bin/env bash
# Vendor PAKT's pure-Python package at the ref pinned in pakt.lock.
# Usage: ops/vendor-pakt.sh [dest]   (default: vendor/ in the project)
# Fetches with git, so a private PAKT repo works with your local git credentials.
# The result holds pakt/ plus content-types.toml, which lives outside the package, so pip would skip it.
set -euo pipefail

PROJECT="$(cd "$(dirname "$0")/.." && pwd)"
lock_get() { { grep -E "^$1=" "$PROJECT/pakt.lock" || true; } | tail -1 | cut -d= -f2-; }
REPO="${PAKT_REPO:-$(lock_get PAKT_REPO)}"
REF="${PAKT_REF:-$(lock_get PAKT_REF)}"
DEST="${1:-$PROJECT/vendor}"
[[ -n "$REPO" && -n "$REF" ]] || { echo "Set PAKT_REPO and PAKT_REF in pakt.lock" >&2; exit 1; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
git init -q "$WORK/src"
git -C "$WORK/src" fetch -q --depth 1 "$REPO" "$REF"
git -C "$WORK/src" -c advice.detachedHead=false checkout -q FETCH_HEAD
SHA="$(git -C "$WORK/src" rev-parse HEAD)"
if [[ "$REF" =~ ^[0-9a-f]{40}$ && "$SHA" != "$REF" ]]; then
  echo "Fetched $SHA, expected $REF" >&2
  exit 1
fi

mkdir -p "$WORK/out"
cp -R "$WORK/src/pakt" "$WORK/out/pakt"
cp "$WORK/src/content-types.toml" "$WORK/out/"
find "$WORK/out" -name __pycache__ -prune -exec rm -rf {} +
echo "$SHA" > "$WORK/out/PAKT_REF"

mkdir -p "$(dirname "$DEST")"
rm -rf "$DEST.new"
cp -R "$WORK/out" "$DEST.new"
rm -rf "$DEST"
mv "$DEST.new" "$DEST"
echo "PAKT $SHA vendored to $DEST"
