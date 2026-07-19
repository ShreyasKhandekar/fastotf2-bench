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
| **Python** (`otf2` + `pyarrow`) | ✓ | ✓ | pure-Python reader, thread-parallel per location |
| **C** (OTF2 C library) | ✓ | — | `fprintf` CSV writer, single-threaded |

C emits CSV only: Parquet output in C requires the Apache Arrow C++/GLib toolchain, which
is a heavy dependency and out of scope for the baseline. The table shows **N/A** there.

## Design: one portable container

Instead of the old approach (a `fast-OTF2` git submodule plus manual installs of the OTF2
C API and the `otf2` Python module), all three converters live inside **one Apptainer
image** built `FROM` the published `fastotf2` converter container. That base already
provides the Chapel converter, the OTF2 3.1.1 C library, and Apache Arrow; the bench image
adds the Python OTF2 stack and compiles the C converter against the same OTF2.

The only host requirements are **Apptainer** and **SLURM**. As long as the trace paths you
set in the notebook are valid, the workflow runs unchanged on any machine.

## Repository layout

```
fastotf2-benchmark.ipynb       # THE control surface: all config + one-click run + analysis
container/
  Containerfile.bench          # FROM fastotf2 image; adds Python stack + compiled C
  build-bench-image.sh         # podman build -> apptainer .sif (env-driven, idempotent)
converters/
  python/otf2_convert.py       # OTF2 -> CSV/Parquet via the otf2 reader + pyarrow
  c/otf2csv.c, Makefile        # OTF2 -> CSV via the OTF2 C library
benchmark/
  run_one.sh                   # env-driven engine: runs ONE conversion, writes one timing CSV
out/run_<timestamp>/           # one self-contained folder per run (created by the notebook)
  config.json, manifest.csv, results.csv, timings/, slurm_logs/, run_logs/, scratch/, plots/
copilot/new-bench-plan.md      # rearchitecture plan
```

There is **no config file** — all configuration lives in the notebook's config cell. Each
`(trace, tool, format, repeat)` is submitted as its **own exclusive single-node SLURM job**
(via a generated per-job `run_logs/<tag>.sbatch`), so conversions run **in parallel** on
separate nodes — the slow Python CSV and Parquet runs proceed simultaneously. Every job
writes its own row to `timings/<tag>.csv`; the notebook merges them into `results.csv`.

## Quick start

```bash
# From the repo root, open and Run All:
jupyter lab fastotf2-benchmark.ipynb
```

Editing the **§0 config cell** is all you normally touch: traces, tool/format combos,
repeats, image path, and SLURM account/partition. Each run of that cell creates a fresh
`out/run_<timestamp>/` folder, so runs never clobber each other.

### Runs, logs, and re-analysis

- **New run:** Run All. A new `out/run_<timestamp>/` is created and submitted to SLURM.
- **Re-analyse a previous run** (no new data): restart the kernel, run the §0 imports, then
  set `ANALYZE_RUN = "run_YYYYMMDD_HHMMSS"` in §4 and run the analysis cells.
- **Resubmit while another run is going:** launch a fresh run any time — the running SLURM
  job is independent and writes to its own folder.
- **Preview without submitting:** set `DRY_RUN = True` in §0.

### Testing a rebuilt image without disturbing a running job

Build to a separate `.sif` and point the notebook's `IMAGE` at it:

```bash
BENCH_SIF=container/fastotf2-bench-next.sif \
BENCH_IMAGE_TAG=localhost/fastotf2-bench:next \
  bash container/build-bench-image.sh --force
```

## Methodology

Each conversion runs inside the bench `.sif` as its **own exclusive single-node SLURM job**,
so jobs run in parallel on separate nodes with clean, uncontended timings. Wall-clock time is
measured around each converter invocation; output goes to that job's `scratch/<tag>` and is
deleted afterwards. Chapel is given all node cores (`CHPL_RT_NUM_THREADS_PER_LOCALE`) — its
data-parallel advantage. Python is also parallel: a thread pool reads each location with its
own reader and writes each output file concurrently (`--jobs`, 64 by default). C runs
single-threaded, as shipped. Per-job data
lands in `out/run_<timestamp>/timings/<tag>.csv` and is merged into `results.csv` (columns:
`run_tag, trace, tool, format, repeat, seconds, output_bytes, status`). Graphs are drawn with
**plotnine** (log10 axis for conversion time; y-from-0 bars for speedup over Python).

## Converter output schema

All converters follow the `fastotf2` schema:

- `<Group>_<Thread>_callgraph.{csv,parquet}` — `Thread, Group, Depth, Name, Start Time,
  End Time, Duration`
- `<Group>_metrics.{csv,parquet}` — `Group, Metric Name, Time, Value`

CSV times are in seconds; Parquet times are in nanoseconds (matching the Chapel converter).
