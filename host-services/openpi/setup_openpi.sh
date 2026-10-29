#!/usr/bin/env bash
# SPDX-License-Identifier: MulanPSL-2.0
# Reconstruct the host-side openpi checkout that backs the VLA policy server
# (:8002, run_openpi_server.sh). VLA is PARKED (pick stays mode: pipeline), so
# this is OPTIONAL — only needed to rebuild the openpi env on a fresh machine.
#
# WHY (self-contained, but VLA-optional)
# --------------------------------------
# openpi is a big framework (~hundreds of MB + its own CUDA/JAX venv), so it is
# NOT vendored in git. It IS reconstructible: this script clones the PINNED
# commit through the domestic gh-proxy and builds its venv with `uv sync`, the
# method the upstream README prescribes. The trained pi05_piper CHECKPOINT is a
# separate, non-downloadable local artifact — see TEST_GUIDE §0.5 for that.
#
# Safe by default: if OPENPI_DIR already exists we DO NOT touch it (never clobber
# a live openpi). Point OPENPI_DIR at a new path to rebuild elsewhere.
set -euo pipefail

# openpi checkout location (matches run_openpi_server.sh's default):
# 本子目录下，clone 完是 host-services/openpi/openpi-src（.gitignore 已忽略）。
HS="$(cd "$(dirname "$0")" && pwd)"
: "${OPENPI_DIR:=$HS/openpi-src}"
# Pinned upstream commit verified working with the pi05_piper checkpoint.
: "${OPENPI_COMMIT:=15a9616a00943ada6c20a0f158e3adb39df2ccac}"
# Domestic proxy prefix so github is reachable; submodule URLs get rewritten too.
: "${GH_PROXY:=https://hk.gh-proxy.org/}"
: "${OPENPI_REPO:=${GH_PROXY}https://github.com/Physical-Intelligence/openpi.git}"

command -v git >/dev/null 2>&1 || { echo "error: 'git' not on PATH" >&2; exit 1; }
command -v uv  >/dev/null 2>&1 || { echo "error: 'uv' not on PATH"  >&2; exit 1; }

if [[ -d "$OPENPI_DIR/.git" ]]; then
    echo "[openpi] existing checkout at $OPENPI_DIR — leaving it untouched."
    echo "[openpi]   (delete it or set OPENPI_DIR=<new path> to rebuild)"
    exit 0
fi

echo "[openpi] cloning $OPENPI_REPO → $OPENPI_DIR"
git clone "$OPENPI_REPO" "$OPENPI_DIR"
cd "$OPENPI_DIR"
git checkout "$OPENPI_COMMIT"

echo "[openpi] rewriting submodule URLs through gh-proxy and fetching them"
# .gitmodules points at raw github; route through the proxy so they resolve too.
git config --file .gitmodules --get-regexp 'submodule\..*\.url' | while read -r key url; do
    case "$url" in
        "$GH_PROXY"*) : ;;                                  # already proxied
        https://github.com/*) git config --file .gitmodules "$key" "${GH_PROXY}${url}" ;;
    esac
done
git submodule sync --recursive
git submodule update --init --recursive

echo "[openpi] building venv (GIT_LFS_SKIP_SMUDGE=1 uv sync; jax[cuda12]==0.5.3, torch==2.7.1)"
GIT_LFS_SKIP_SMUDGE=1 uv sync
GIT_LFS_SKIP_SMUDGE=1 uv pip install -e .

echo "[openpi] verifying import"
.venv/bin/python -c "import jax, torch; print('[openpi]   jax', jax.__version__, '| torch', torch.__version__)"

echo "[openpi] done. Serve the VLA policy with:"
echo "[openpi]   OPENPI_DIR=$OPENPI_DIR bash $(cd "$(dirname "$0")" && pwd)/run_openpi_server.sh"
echo "[openpi]   (also needs the pi05_piper checkpoint — see TEST_GUIDE §0.5)"
