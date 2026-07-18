#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# run_one.sh -- run ONE converter conversion and record its timing.
#
# Designed so the notebook can submit each (trace x tool/format x repeat) as its
# OWN SLURM job -> conversions run in parallel, each on its own exclusive node,
# so e.g. the Python CSV and Python Parquet runs proceed simultaneously instead
# of back-to-back. Each job writes its own one-row timing CSV (no shared-file
# race); the notebook merges them for analysis.
#
# No config file: every parameter is an environment variable (baked into a
# per-job sbatch script by the notebook).
#
# Required:
#   BENCH_SIF          Apptainer .sif to run
#   BENCH_TRACE_DIR    directory holding the OTF2 anchor
#   BENCH_TRACE_LABEL  label for this trace (e.g. "11.6GiB"; no spaces)
#   BENCH_TOOL         fastotf2 | python | c   ("chapel" accepted as an alias)
#   BENCH_FORMAT       CSV | PARQUET
#   BENCH_OUTPUT_DIR   scratch dir for this conversion's output (created; deleted after)
#   BENCH_RESULT_FILE  one-row CSV written here
#   BENCH_RUN_TAG      run tag string
# Optional:
#   BENCH_REPEAT=1  BENCH_TRACE_ANCHOR=traces.otf2  BENCH_CHPL_THREADS=<nproc>
#   BENCH_IN_PYTHON=/opt/bench/otf2_convert.py  BENCH_IN_C=/opt/bench/otf2csv
# ---------------------------------------------------------------------------
set -uo pipefail

: "${BENCH_REPEAT:=1}"
: "${BENCH_TRACE_ANCHOR:=traces.otf2}"
: "${BENCH_IN_PYTHON:=/opt/bench/otf2_convert.py}"
: "${BENCH_IN_C:=/opt/bench/otf2csv}"

for v in BENCH_SIF BENCH_TRACE_DIR BENCH_TRACE_LABEL BENCH_TOOL BENCH_FORMAT \
         BENCH_OUTPUT_DIR BENCH_RESULT_FILE BENCH_RUN_TAG; do
    if [[ -z "${!v:-}" ]]; then echo "ERROR: required env var $v is not set" >&2; exit 2; fi
done
if [[ ! -f "${BENCH_SIF}" ]]; then echo "ERROR: sif not found: ${BENCH_SIF}" >&2; exit 1; fi

if [[ "${BENCH_CHPL_THREADS:-0}" -gt 0 ]]; then
    export CHPL_RT_NUM_THREADS_PER_LOCALE="${BENCH_CHPL_THREADS}"
fi

rm -rf "${BENCH_OUTPUT_DIR}"; mkdir -p "${BENCH_OUTPUT_DIR}" "$(dirname "${BENCH_RESULT_FILE}")"

anchor="/data/${BENCH_TRACE_ANCHOR}"
bind=(--bind "${BENCH_TRACE_DIR}:/data:ro" --bind "${BENCH_OUTPUT_DIR}:/out")
case "${BENCH_TOOL}" in
    fastotf2|chapel) cmd=(apptainer run "${bind[@]}" "${BENCH_SIF}"
                 "${anchor}" --format="${BENCH_FORMAT}" --outputDir=/out --log=ERROR) ;;
    python) cmd=(apptainer exec "${bind[@]}" "${BENCH_SIF}"
                 python3 "${BENCH_IN_PYTHON}" "${anchor}" --format "${BENCH_FORMAT}" --outputDir /out) ;;
    c)      cmd=(apptainer exec "${bind[@]}" "${BENCH_SIF}"
                 "${BENCH_IN_C}" "${anchor}" --outputDir /out) ;;
    *)      echo "Unknown tool: ${BENCH_TOOL}" >&2; exit 2 ;;
esac

echo "=============================================================="
echo " ${BENCH_TRACE_LABEL} | ${BENCH_TOOL} | ${BENCH_FORMAT} | rep ${BENCH_REPEAT}"
echo " node: $(hostname)   run_tag: ${BENCH_RUN_TAG}"
echo " cmd:  ${cmd[*]}"
echo " start: $(date)"
echo "=============================================================="

start=$(date +%s.%N)
"${cmd[@]}"
status=$?
end=$(date +%s.%N)

seconds=$(awk "BEGIN{printf \"%.3f\", ${end}-${start}}")
bytes=$(du -sb "${BENCH_OUTPUT_DIR}" 2>/dev/null | cut -f1); bytes="${bytes:-0}"

echo "run_tag,trace,tool,format,repeat,seconds,output_bytes,status" > "${BENCH_RESULT_FILE}"
echo "${BENCH_RUN_TAG},${BENCH_TRACE_LABEL},${BENCH_TOOL},${BENCH_FORMAT},${BENCH_REPEAT},${seconds},${bytes},${status}" >> "${BENCH_RESULT_FILE}"

rm -rf "${BENCH_OUTPUT_DIR}"
echo "done: ${seconds}s, ${bytes} bytes, status=${status}"
exit ${status}
