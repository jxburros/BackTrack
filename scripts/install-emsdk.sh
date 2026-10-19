#!/usr/bin/env bash
# Installs the pinned Emscripten SDK for the WebAssembly build (wasm/README.md).
#   scripts/install-emsdk.sh DIR      then: source DIR/emsdk_env.sh
set -euo pipefail
EMSDK_VERSION=6.0.13
dir="${1:?usage: scripts/install-emsdk.sh DIR}"
[ -d "$dir/.git" ] || git clone --depth 1 https://github.com/emscripten-core/emsdk.git "$dir"
"$dir/emsdk" install "$EMSDK_VERSION"
"$dir/emsdk" activate "$EMSDK_VERSION"
