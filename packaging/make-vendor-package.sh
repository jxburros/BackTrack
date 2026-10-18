#!/usr/bin/env bash
# Builds a vendor source package: a pinned upstream source tree, pruned to
# what the build needs, packed as an npm tarball. What gets pinned and pruned
# comes from a spec in packaging/vendor/ — llama.cpp.conf (onyx-engine's
# llama.cpp) by default, or ggml.conf (standalone ggml, the pin the BackTrack
# audio apps use). Another repository reuses this script, lib.sh and
# scripts/vendor.sh unchanged by adding its own spec file.
#
# This is the MAINTAINER packaging step (run once per pin upgrade, then
# `npm publish` the tarball). It is also the local fallback `npm run vendor`
# (scripts/vendor.sh) uses to stage a tree from a checkout you already have.
#
# Usage:
#   packaging/make-vendor-package.sh [--spec NAME|FILE] [path-to-upstream-checkout]
#
# Without a path the pinned commit is fetched and verified with git.
set -euo pipefail

cd "$(dirname "$0")/.."
. packaging/vendor/lib.sh

SPEC="${VENDOR_SPEC:-llama.cpp}"
SRC=""
while [ $# -gt 0 ]; do
    case "$1" in
        --spec) SPEC="${2:?--spec needs a value}"; shift 2 ;;
        --spec=*) SPEC="${1#--spec=}"; shift ;;
        -h|--help) sed -n '2,17p' "$0"; exit 0 ;;
        *) [ -z "$SRC" ] || { echo "error: unexpected argument '$1'" >&2; exit 1; }
           SRC="$1"; shift ;;
    esac
done
load_vendor_spec "$SPEC"

DIST="$VENDOR_DIST"
STAGE="$DIST/package"

# Fetch the pinned commit with git. Some networks (corporate proxies, the
# sandboxes CI and agents run in) allow `git clone` over HTTPS but return
# 403 for `github.com/.../archive/*.tar.gz`; a depth-1 fetch of the exact
# commit costs about the same as the tarball and pins identically.
fetch_with_git() {
    local dest="$1"
    rm -rf "$dest"
    mkdir -p "$dest"
    git init -q "$dest"
    git -C "$dest" remote add origin "$VENDOR_UPSTREAM"
    if [ -n "$VENDOR_SPARSE" ]; then
        git -C "$dest" sparse-checkout init --cone
        # shellcheck disable=SC2086
        git -C "$dest" sparse-checkout set $VENDOR_SPARSE
    fi
    git -C "$dest" fetch --depth 1 -q origin "$VENDOR_COMMIT"
    git -C "$dest" checkout -q FETCH_HEAD
    rm -rf "$dest/.git"
}

if [ -z "$SRC" ]; then
    SRC="$DIST/$VENDOR_NAME-$VENDOR_COMMIT"
    mkdir -p "$DIST"
    echo "fetching verified $VENDOR_NAME commit $VENDOR_COMMIT ..."
    fetch_with_git "$SRC"
else
    actual=$(git -C "$SRC" rev-parse HEAD 2>/dev/null) || {
        echo "error: supplied $VENDOR_NAME source must be a Git checkout" >&2
        exit 1
    }
    [ "$actual" = "$VENDOR_COMMIT" ] || {
        echo "error: supplied $VENDOR_NAME source is $actual, expected $VENDOR_COMMIT" >&2
        exit 1
    }
fi
[ -f "$SRC/CMakeLists.txt" ] || { echo "error: '$SRC' is not a $VENDOR_NAME source tree"; exit 1; }

echo "staging from $SRC ..."
rm -rf "$STAGE"
mkdir -p "$STAGE"

# Everything the library build needs, minus the spec's VENDOR_PRUNE list,
# with VENDOR_KEEP copied back afterwards. For llama.cpp that drops docs,
# tests, examples and the like, all of models/ except the vocab file
# scripts/make-tiny-model.py reads, and all of tools/ except tools/mtmd (the
# one library onyx-engine builds out of it, via -DLLAMA_BUILD_MTMD=ON).
cp -a "$SRC/." "$STAGE/"
rm -rf "$STAGE/.git"
for p in $VENDOR_PRUNE; do
    rm -rf "${STAGE:?}/$p"
done
for p in $VENDOR_KEEP; do
    [ -e "$SRC/$p" ] || { echo "error: $VENDOR_NAME source has no '$p' to keep" >&2; exit 1; }
    mkdir -p "$STAGE/$(dirname "$p")"
    cp -a "$SRC/$p" "$STAGE/$p"
done
for p in $VENDOR_REQUIRED; do
    [ -e "$STAGE/$p" ] || { echo "error: staged $VENDOR_NAME tree is missing '$p'" >&2; exit 1; }
done

# npm pack honors .gitignore files unless .npmignore exists; upstream's
# .gitignore would silently drop files (e.g. *.gguf), so remove them and ship
# an explicit empty-ish .npmignore instead.
find "$STAGE" -name '.gitignore' -delete
printf '# intentionally minimal - the staging script already pruned the tree\n' > "$STAGE/.npmignore"
printf '%s\n' "$VENDOR_COMMIT" > "$STAGE/.vendor-source-commit"

cat > "$STAGE/package.json" <<JSON
{
  "name": "$PKG_NAME",
  "version": "$PKG_VERSION",
  "description": "Pinned, pruned $VENDOR_NAME source tree consumed by a CMake build. Not a Node.js library - contains C/C++ sources only.",
  "license": "MIT",
  "homepage": "$VENDOR_UPSTREAM",
  "repository": {
    "type": "git",
    "url": "git+$PKG_REPOSITORY.git",
    "directory": "packaging"
  },
  "vendorName": "$VENDOR_NAME",
  "vendorCommit": "$VENDOR_COMMIT",
  "vendorUpstream": "$VENDOR_UPSTREAM"
}
JSON

echo "packing ..."
( cd "$DIST" && npm pack ./package --silent )

[ -f "$VENDOR_TARBALL" ] || { echo "error: expected package $VENDOR_TARBALL was not created" >&2; exit 1; }
echo
echo "wrote  $VENDOR_TARBALL"
du -sh "$STAGE" "$VENDOR_TARBALL" | sed 's/^/  /'
echo
echo "to publish:  npm publish $VENDOR_TARBALL --access public"
