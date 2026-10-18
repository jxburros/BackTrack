#!/usr/bin/env bash
# Stage a pinned vendor source package and install it where CMake looks for
# it (node_modules/<PKG_NAME>). The spec (packaging/vendor/<name>.conf)
# says what is pinned; llama.cpp is the default.
#
#   npm run vendor                              # stage + install llama.cpp
#   npm run vendor -- /path/to/llama.cpp        # reuse a checkout you have
#   bash scripts/vendor.sh --spec ggml          # another spec
#   bash scripts/vendor.sh --install-only       # install an already-staged
#                                               # tarball (CI restores it from cache)
#
# With no path the pinned commit is fetched from GitHub by
# packaging/make-vendor-package.sh (a depth-1 git fetch). It requires Bash,
# Git, npm (for packaging), and tar (for extraction).
set -euo pipefail

cd "$(dirname "$0")/.."
. packaging/vendor/lib.sh

SPEC="${VENDOR_SPEC:-llama.cpp}"
INSTALL_ONLY=0
PASS=()
while [ $# -gt 0 ]; do
    case "$1" in
        --spec) SPEC="${2:?--spec needs a value}"; shift 2 ;;
        --spec=*) SPEC="${1#--spec=}"; shift ;;
        --install-only) INSTALL_ONLY=1; shift ;;
        -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
        *) PASS+=("$1"); shift ;;
    esac
done
load_vendor_spec "$SPEC"

if [ "$INSTALL_ONLY" = 0 ]; then
    bash packaging/make-vendor-package.sh --spec "$VENDOR_SPEC_FILE" "${PASS[@]+"${PASS[@]}"}"
fi
[ -f "$VENDOR_TARBALL" ] || { echo "error: expected package $VENDOR_TARBALL was not created" >&2; exit 1; }

DEST="$VENDOR_INSTALL_DIR"
echo "installing $VENDOR_TARBALL -> $DEST ..."
rm -rf "$DEST"
mkdir -p "$DEST"
tar -xzf "$VENDOR_TARBALL" --strip-components=1 -C "$DEST"

for p in $VENDOR_REQUIRED; do
    [ -e "$DEST/$p" ] || { echo "error: $DEST/$p missing after extraction" >&2; exit 1; }
done

echo
echo "vendored $VENDOR_NAME source ready in $DEST"
