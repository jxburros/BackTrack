# Shared by packaging/make-vendor-package.sh and scripts/vendor.sh: resolve a
# vendor spec and derive the names every reader agrees on. Source it from
# the repository root.

# load_vendor_spec NAME|PATH — NAME is a file in packaging/vendor/<NAME>.conf.
load_vendor_spec() {
    local spec="$1" file
    case "$spec" in
        */*|*.conf) file="$spec" ;;
        *) file="packaging/vendor/$spec.conf" ;;
    esac
    [ -f "$file" ] || { echo "error: vendor spec '$spec' not found ($file)" >&2; return 1; }
    VENDOR_NAME="" VENDOR_UPSTREAM="" VENDOR_COMMIT="" VENDOR_UPSTREAM_VERSION=""
    VENDOR_BUILD="" PKG_NAME="" PKG_REPOSITORY="" VENDOR_SPARSE="" VENDOR_PRUNE="" VENDOR_KEEP="" VENDOR_REQUIRED=""
    # shellcheck disable=SC1090
    . "$file"
    local v
    for v in VENDOR_NAME VENDOR_UPSTREAM VENDOR_COMMIT VENDOR_UPSTREAM_VERSION PKG_NAME; do
        [ -n "${!v}" ] || { echo "error: $file does not set $v" >&2; return 1; }
    done
    [[ "$VENDOR_COMMIT" =~ ^[0-9a-f]{40}$ ]] || {
        echo "error: $file: VENDOR_COMMIT must be 40 hex characters" >&2; return 1; }
    VENDOR_SPEC_FILE="$file"
    # The repository that packages it (package.json "repository").
    PKG_REPOSITORY="${PKG_REPOSITORY:-https://github.com/JX-Holdings-LLC/Onyx-Engine}"
    # <upstream version>[-b<build>].g<short commit>, e.g. 0.3.0-b10711.g9723942ad
    PKG_VERSION="$VENDOR_UPSTREAM_VERSION${VENDOR_BUILD:+-b$VENDOR_BUILD}.g${VENDOR_COMMIT:0:9}"
    # npm pack's file name: scope '@' dropped, '/' becomes '-'.
    local base="${PKG_NAME#@}"
    VENDOR_DIST="packaging/dist/$VENDOR_NAME"
    VENDOR_TARBALL="$VENDOR_DIST/${base//\//-}-$PKG_VERSION.tgz"
    VENDOR_INSTALL_DIR="node_modules/$PKG_NAME"
}
