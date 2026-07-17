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
# All parameters are environment variables (no config file). Override any of:
#   BENCH_SIF         output .sif path      (default: <repo>/container/fastotf2-bench.sif)
#   BENCH_IMAGE_TAG   podman image tag      (default: localhost/fastotf2-bench:latest)
#   BENCH_BASE_IMAGE  base fastotf2 image   (default: portable single-locale converter)
#
# Example (build a second image without touching the default one):
#   BENCH_SIF=container/fastotf2-bench-next.sif \
#   BENCH_IMAGE_TAG=localhost/fastotf2-bench:next \
#     bash container/build-bench-image.sh --force
# ---------------------------------------------------------------------------
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

BENCH_SIF="${BENCH_SIF:-${REPO_DIR}/container/fastotf2-bench.sif}"
BENCH_IMAGE_TAG="${BENCH_IMAGE_TAG:-localhost/fastotf2-bench:latest}"
BENCH_BASE_IMAGE="${BENCH_BASE_IMAGE:-ghcr.io/hpc-ai-adv-dev/fastotf2/fastotf2-converter:latest}"

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
