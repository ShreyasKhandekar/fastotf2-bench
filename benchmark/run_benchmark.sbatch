#!/usr/bin/env bash
#SBATCH --job-name=fastotf2-bench
#SBATCH --nodes=1
#SBATCH --exclusive
#SBATCH --time=12:00:00
#SBATCH --output=results/logs/slurm-%j.out
#SBATCH --error=results/logs/slurm-%j.err
# ---------------------------------------------------------------------------
# run_benchmark.sbatch -- run the full converter comparison on ONE exclusive
# node so every timing is comparable and uncontended.
#
# Account, partition and time limit come from bench.config.sh but SLURM reads
# #SBATCH lines before the script body runs, so submit with the helper flags
# that the notebook uses, or override on the command line, e.g.:
#
#   sbatch --account=normal_users --partition=hotlum benchmark/run_benchmark.sbatch
#
# Everything else (traces, combos, repeats, image, output root) is read from
# bench.config.sh at runtime.
# ---------------------------------------------------------------------------
set -uo pipefail

# Resolve repo dir whether launched via sbatch (SLURM_SUBMIT_DIR) or directly.
REPO_DIR="${SLURM_SUBMIT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "${REPO_DIR}"
mkdir -p results/logs

echo "=============================================================="
echo " fastotf2 converter benchmark"
echo " node:    $(hostname)"
echo " job:     ${SLURM_JOB_ID:-<none>}"
echo " date:    $(date)"
echo "=============================================================="

bash "${REPO_DIR}/benchmark/benchmark.sh"
