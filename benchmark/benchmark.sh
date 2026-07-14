#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# benchmark.sh -- run the converter comparison and record timings.
#
# For every (trace x tool/format x repeat) it runs the converter inside the
# bench container via Apptainer, measures wall-clock time, records the output
# size, then cleans up. Results are appended to $BENCH_RESULTS_CSV as:
#
#   trace,tool,format,repeat,seconds,output_bytes,status
#
# Runs standalone or inside the SLURM job (run_benchmark.sbatch). All config
# comes from ../bench.config.sh (override via environment).
# ---------------------------------------------------------------------------
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=/dev/null
source "${REPO_DIR}/bench.config.sh"

if [[ ! -f "${BENCH_SIF}" ]]; then
    echo "ERROR: bench image not found at ${BENCH_SIF}" >&2
    echo "Build it first: bash ${REPO_DIR}/container/build-bench-image.sh" >&2
    exit 1
fi

mkdir -p "${BENCH_OUTPUT_ROOT}" "${BENCH_RESULTS_DIR}" "${BENCH_RESULTS_DIR}/logs"

# Chapel's data-parallel advantage: give it all requested threads.
if [[ "${BENCH_CHPL_THREADS}" -gt 0 ]]; then
    export CHPL_RT_NUM_THREADS_PER_LOCALE="${BENCH_CHPL_THREADS}"
fi

# Fresh results file with header.
echo "trace,tool,format,repeat,seconds,output_bytes,status" > "${BENCH_RESULTS_CSV}"

echo "Benchmark start: $(date)"
echo "Image:   ${BENCH_SIF}"
echo "Output:  ${BENCH_OUTPUT_ROOT}"
echo "Results: ${BENCH_RESULTS_CSV}"
echo "Chapel threads/locale: ${CHPL_RT_NUM_THREADS_PER_LOCALE:-default}"
echo

run_one() {
    local trace_label="$1" trace_dir="$2" tool="$3" fmt="$4" rep="$5"
    local anchor="${trace_dir}/${BENCH_TRACE_ANCHOR}"
    local tag="${trace_label}_${tool}_${fmt}_r${rep}"
    local outdir="${BENCH_OUTPUT_ROOT}/${tag}"
    local log="${BENCH_RESULTS_DIR}/logs/${tag}.log"

    rm -rf "${outdir}"; mkdir -p "${outdir}"

    local -a bind=(--bind "${trace_dir}:/data:ro" --bind "${outdir}:/out")
    local -a cmd
    case "${tool}" in
        chapel) cmd=(apptainer run "${bind[@]}" "${BENCH_SIF}"
                     /data/"${BENCH_TRACE_ANCHOR}" --format="${fmt}" --outputDir=/out --log=ERROR) ;;
        python) cmd=(apptainer exec "${bind[@]}" "${BENCH_SIF}"
                     python3 "${BENCH_IN_PYTHON}" /data/"${BENCH_TRACE_ANCHOR}" --format "${fmt}" --outputDir /out) ;;
        c)      cmd=(apptainer exec "${bind[@]}" "${BENCH_SIF}"
                     "${BENCH_IN_C}" /data/"${BENCH_TRACE_ANCHOR}" --outputDir /out) ;;
        *)      echo "Unknown tool: ${tool}" >&2; return 1 ;;
    esac

    echo ">>> ${tag}"
    local start end status bytes
    start=$(date +%s.%N)
    "${cmd[@]}" > "${log}" 2>&1
    status=$?
    end=$(date +%s.%N)

    local seconds
    seconds=$(awk "BEGIN{printf \"%.3f\", ${end}-${start}}")
    bytes=$(du -sb "${outdir}" 2>/dev/null | cut -f1); bytes="${bytes:-0}"

    if [[ ${status} -ne 0 ]]; then
        echo "    FAILED (exit ${status}) -- see ${log}"
    fi
    echo "    ${seconds}s, ${bytes} bytes, status=${status}"
    echo "${trace_label},${tool},${fmt},${rep},${seconds},${bytes},${status}" >> "${BENCH_RESULTS_CSV}"

    rm -rf "${outdir}"
}

for trace_entry in "${BENCH_TRACES[@]}"; do
    trace_label="${trace_entry%%|*}"
    trace_dir="${trace_entry##*|}"
    if [[ ! -f "${trace_dir}/${BENCH_TRACE_ANCHOR}" ]]; then
        echo "WARNING: skipping ${trace_label}: ${trace_dir}/${BENCH_TRACE_ANCHOR} not found" >&2
        continue
    fi
    for combo in "${BENCH_COMBOS[@]}"; do
        tool="${combo%%|*}"
        fmt="${combo##*|}"
        for rep in $(seq 1 "${BENCH_REPEATS}"); do
            run_one "${trace_label}" "${trace_dir}" "${tool}" "${fmt}" "${rep}"
        done
    done
done

echo
echo "Benchmark done: $(date)"
echo "Results written to ${BENCH_RESULTS_CSV}"
