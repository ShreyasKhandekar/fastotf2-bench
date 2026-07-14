# fast-OTF2-bench

Benchmarks the **Chapel-powered [`fastotf2`](https://github.com/hpc-ai-adv-dev/fastotf2)
converter** against **Python** and **C** converters, measuring the wall-clock time each
takes to convert OTF2 traces into tabular output (CSV / Parquet).

Everything runs from **one Jupyter notebook**: [`fastotf2-benchmark.ipynb`](fastotf2-benchmark.ipynb).
Choose *Run All* and it builds the container (once), submits a single-node SLURM job that
times every converter, and renders a comparison table + chart.

## What is compared

| Tool | CSV | Parquet | How it runs |
|------|-----|---------|-------------|
| **Chapel** (`fastotf2`) | ✓ | ✓ | the upstream converter, using all node cores |
| **Python** (`otf2` + `pyarrow`) | ✓ | ✓ | pure-Python reader, single-threaded |
| **C** (OTF2 C library) | ✓ | — | `fprintf` CSV writer, single-threaded |

C emits CSV only: Parquet output in C requires the Apache Arrow C++/GLib toolchain, which
is a heavy dependency and out of scope for the baseline. The table shows **N/A** there.

## Design: one portable container

Instead of the old approach (a `fast-OTF2` git submodule plus manual installs of the OTF2
C API and the `otf2` Python module), all three converters live inside **one Apptainer
image** built `FROM` the published `fastotf2` converter container. That base already
provides the Chapel converter, the OTF2 3.1.1 C library, and Apache Arrow; the bench image
adds the Python OTF2 stack and compiles the C converter against the same OTF2.

The only host requirements are **Apptainer** and **SLURM**. As long as the trace paths in
[`bench.config.sh`](bench.config.sh) are valid, the workflow runs unchanged on any machine.

## Repository layout

```
bench.config.sh                # single source of truth: traces, image, paths, SLURM
container/
  Containerfile.bench          # FROM fastotf2 image; adds Python stack + compiled C
  build-bench-image.sh         # podman build -> apptainer .sif (idempotent)
converters/
  python/otf2_convert.py       # OTF2 -> CSV/Parquet via the otf2 reader + pyarrow
  c/otf2csv.c, Makefile        # OTF2 -> CSV via the OTF2 C library
benchmark/
  benchmark.sh                 # core timing loop (tool x format x trace x repeat)
  run_benchmark.sbatch         # SLURM wrapper: 1 exclusive node -> results/results.csv
results/                       # results.csv, rendered table, chart (generated)
fastotf2-benchmark.ipynb       # ONE-CLICK: build -> submit -> table + chart
copilot/new-bench-plan.md      # rearchitecture plan
```

## Quick start

```bash
# From the repo root, open and Run All:
jupyter lab fastotf2-benchmark.ipynb
```

Or run the pieces directly:

```bash
# 1. Build the benchmark container (once)
bash container/build-bench-image.sh

# 2. Run the benchmark on one exclusive node
sbatch --account=<acct> --partition=<part> benchmark/run_benchmark.sbatch

# 3. Inspect results
column -s, -t results/results.csv
```

## Configuration

Edit [`bench.config.sh`](bench.config.sh) (or override via environment variables):

- `BENCH_TRACES` — `label|/path/to/trace-dir` entries. Defaults to the Frontier
  `2-node` (~699 MB) and `32-node` (~33 GB) single-HPL runs on the shared filesystem.
- `BENCH_COMBOS` — which `tool|format` pairs to run.
- `BENCH_REPEATS` — timed repeats per combination (averaged; default 1).
- `BENCH_SIF`, `BENCH_BASE_IMAGE` — container image locations.
- `BENCH_OUTPUT_ROOT`, `BENCH_RESULTS_DIR` — scratch and results directories.
- `BENCH_SLURM_ACCOUNT`, `BENCH_SLURM_PARTITION`, `BENCH_SLURM_TIME` — SLURM settings.

## Methodology

Every conversion runs inside `container/fastotf2-bench.sif` on **one exclusive node** so
timings are comparable and uncontended. Wall-clock time is measured around each converter
invocation; output goes to scratch and is deleted between runs. Chapel is given all node
cores (`CHPL_RT_NUM_THREADS_PER_LOCALE`) — its data-parallel advantage — while Python and C
run single-threaded, as shipped. Raw per-run data lands in `results/results.csv`.

## Converter output schema

All converters follow the `fastotf2` schema:

- `<Group>_<Thread>_callgraph.{csv,parquet}` — `Thread, Group, Depth, Name, Start Time,
  End Time, Duration`
- `<Group>_metrics.{csv,parquet}` — `Group, Metric Name, Time, Value`

CSV times are in seconds; Parquet times are in nanoseconds (matching the Chapel converter).
