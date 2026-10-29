#!/usr/bin/env bash
set -euo pipefail
PKG="${RBNX_PACKAGE_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"

rbnx codegen -p "$PKG" --mcp
echo "[lynx_chassis] build done"
