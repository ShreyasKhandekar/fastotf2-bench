#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# bench.config.sh — single source of truth for the fastotf2 benchmark.
#
# Every script (build, SLURM job, runner) and the notebook source this file.
# To retarget the benchmark to another machine, edit ONLY this file.
#
# All values can be overridden from the environment, e.g.:
#   BENCH_REPEATS=3 sbatch benchmark/run_benchmark.sbatch
# ---------------------------------------------------------------------------

# Absolute path to this repository (directory containing this file).
BENCH_REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---------------------------------------------------------------------------
# Traces to benchmark.
#
# Each entry is "label|/absolute/path/to/trace-archive-dir". The directory must
# contain the OTF2 anchor file "traces.otf2".
#
# For a quick subset without editing this file, export BENCH_TRACES_STR as a
# space-separated list of the same "label|path" entries, e.g.:
#   BENCH_TRACES_STR="2-node|/path/to/2-node" bash benchmark/benchmark.sh
# ---------------------------------------------------------------------------
if [[ -n "${BENCH_TRACES_STR:-}" ]]; then
  read -r -a BENCH_TRACES <<< "${BENCH_TRACES_STR}"
elif [[ -z "${BENCH_TRACES:-}" ]]; then
  BENCH_TRACES=(
    "2-node|/lus/bnchlu1/adt/otf2-traces/frontier/frontier-2-node-single-HPL-run"
    "32-node|/lus/bnchlu1/adt/otf2-traces/frontier/frontier-32-node-single-HPL-run"
  )
fi
# Name of the OTF2 anchor file inside each trace directory.
: "${BENCH_TRACE_ANCHOR:=traces.otf2}"

# ---------------------------------------------------------------------------
# Which (tool, format) combinations to run, and in what order.
#   Chapel + Python : CSV and PARQUET
#   C               : CSV only
#
# Order matters on large traces: fast tools (chapel, c) run FIRST so their
# results are recorded even if a slow tool (python) is later killed by the
# SLURM time limit on a huge trace. Python -- the slowest by a wide, and
# widening, margin at scale -- runs last.
#
# Override a subset with BENCH_COMBOS_STR (space-separated "tool|format").
# ---------------------------------------------------------------------------
if [[ -n "${BENCH_COMBOS_STR:-}" ]]; then
  read -r -a BENCH_COMBOS <<< "${BENCH_COMBOS_STR}"
elif [[ -z "${BENCH_COMBOS:-}" ]]; then
  BENCH_COMBOS=(
    "chapel|CSV"
    "chapel|PARQUET"
    "c|CSV"
    "python|CSV"
    "python|PARQUET"
  )
fi

# Number of timed repeats per combination (>=1). Traces are large; default 1.
: "${BENCH_REPEATS:=1}"

# ---------------------------------------------------------------------------
# Container image.
#
# BENCH_SIF        : path to the Apptainer .sif the benchmark runs against.
# BENCH_BASE_IMAGE : base fastotf2 image the bench image is built FROM.
#                    Portable single-locale build is ideal for single-node.
# ---------------------------------------------------------------------------
: "${BENCH_SIF:=${BENCH_REPO_DIR}/container/fastotf2-bench.sif}"
: "${BENCH_BASE_IMAGE:=ghcr.io/hpc-ai-adv-dev/fastotf2/fastotf2-converter:latest}"
: "${BENCH_IMAGE_TAG:=localhost/fastotf2-bench:latest}"

# ---------------------------------------------------------------------------
# Scratch / output locations (created on demand, cleaned between runs).
# Conversion output for the 32-node trace can be tens of GB, so keep this on a
# filesystem with room. Override BENCH_SCRATCH to relocate.
# ---------------------------------------------------------------------------
: "${BENCH_SCRATCH:=${BENCH_REPO_DIR}/.bench-scratch}"
: "${BENCH_OUTPUT_ROOT:=${BENCH_SCRATCH}/out}"
: "${BENCH_RESULTS_DIR:=${BENCH_REPO_DIR}/results}"
: "${BENCH_RESULTS_CSV:=${BENCH_RESULTS_DIR}/results.csv}"

# ---------------------------------------------------------------------------
# Chapel runtime threads per locale (its data-parallel advantage). Defaults to
# all cores on the node.
# ---------------------------------------------------------------------------
: "${BENCH_CHPL_THREADS:=$( (nproc) 2>/dev/null || echo 0)}"

# ---------------------------------------------------------------------------
# SLURM settings (used by run_benchmark.sbatch; overridable via env or here).
# ---------------------------------------------------------------------------
: "${BENCH_SLURM_ACCOUNT:=normal_users}"
: "${BENCH_SLURM_PARTITION:=hotlum}"
: "${BENCH_SLURM_TIME:=12:00:00}"
: "${BENCH_SLURM_JOBNAME:=fastotf2-bench}"

# Paths to the in-container converter assets (baked in by Containerfile.bench).
: "${BENCH_IN_PYTHON:=/opt/bench/otf2_convert.py}"
: "${BENCH_IN_C:=/opt/bench/otf2csv}"
