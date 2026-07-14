#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# build-bench-image.sh -- build the fastotf2-bench container and export a .sif.
#
# Steps:
#   1. podman build the bench image FROM the fastotf2 converter base image,
#      adding the Python + C converters (see Containerfile.bench).
#   2. Export the image to an OCI archive and `apptainer build` a .sif.
#
# Idempotent: pass --force to rebuild even if the .sif already exists.
#
# Config (paths, base image, sif location) comes from ../bench.config.sh and can
# be overridden via environment variables.
# ---------------------------------------------------------------------------
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=/dev/null
source "${REPO_DIR}/bench.config.sh"

FORCE=0
[[ "${1:-}" == "--force" ]] && FORCE=1

if [[ -f "${BENCH_SIF}" && "${FORCE}" -eq 0 ]]; then
    echo "Bench image already exists: ${BENCH_SIF}"
    echo "(pass --force to rebuild)"
    exit 0
fi

echo "==> Building bench image FROM ${BENCH_BASE_IMAGE}"
podman build \
    --build-arg "BASE_IMAGE=${BENCH_BASE_IMAGE}" \
    -t "${BENCH_IMAGE_TAG}" \
    -f "${SCRIPT_DIR}/Containerfile.bench" \
    "${REPO_DIR}"

echo "==> Exporting image to OCI archive"
OCI_TAR="$(mktemp -t fastotf2-bench-oci.XXXXXX.tar)"
trap 'rm -f "${OCI_TAR}"' EXIT
podman save --format oci-archive -o "${OCI_TAR}" "${BENCH_IMAGE_TAG}"

echo "==> Building Apptainer SIF: ${BENCH_SIF}"
mkdir -p "$(dirname "${BENCH_SIF}")"
apptainer build --force "${BENCH_SIF}" "oci-archive://${OCI_TAR}"

echo "==> Done: ${BENCH_SIF}"
