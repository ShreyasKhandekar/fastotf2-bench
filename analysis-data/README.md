# analysis-data — small, git-tracked data to RE-RUN the fastotf2-benchmark analysis

Minimal, clean, **non-sensitive** data to re-run this notebook's analysis (§4) on another
machine without the large scratch outputs and without leaking account/system secrets. Mirrors
the scheme in the sibling `fastotf2` repo (`workflows/scaling/analysis-data/`).

## Layout

```
analysis-data/
  README.md
  tools/build_bench_analysis_data.py   # copies the clean subset + SANITIZES config.json
  <system>/                            # neutral label: "other-ex", "frontier", ...
    <run>/results.csv, trace_sizes.json, config.json (sanitized), plots/, SOURCES.md
```

`<system>` is a neutral label (never the real cluster name). A Frontier upload adds a sibling
`frontier/` with the same shape.

## Re-run the analysis from here

C's §4 analysis is **self-contained from `results.csv`**. Restart the kernel, go to §4, set
`ANALYZE_RUN = "<...>/analysis-data/<system>/<run>"`, run the analysis cells.

## Sensitivity — how account/email are handled (important for Frontier)

`config.json` carries a `slurm` block. On this system `account` is `null`, but **on Frontier the
account is real and `extra_args` contains `--account=…` / `--mail-user=…`** — those are sensitive.
`tools/build_bench_analysis_data.py` runs every config through `sanitize_json()`, which redacts:
- sensitive KEYS: `account`, `mail`, `user`, `secret`, `token`, `password`;
- sensitive VALUES: `--account=…` / `--mail-user=…`-style flags and anything that looks like an
  email — anywhere in the JSON.

Safe fields (neutral `system` label, container image ref, filepaths, `time`, `--exclusive`,
`--mail-type=…`) are preserved. Verified on Frontier-style input: `account`, `--account=`,
`--mail-user=…@…`, and a stray email all become `<redacted>`. **Always** build the upload with
this tool (or run the sanitizer yourself) — never copy a raw `config.json` from a system where the
account/mail are set.

Also dropped entirely (bulky and/or sensitive, unused by analysis): `slurm_logs/`, `run_logs/`,
`scratch/`, `manifest.csv`.

## Code-provenance note (RESOLVED 2026-07-21)

This run's **python** numbers are from the **serial** python converter. It had briefly been
replaced in the repo by a "parallel" version that turned out to be GIL-bound (not actually
parallel) -- **fixed**: the serial version is now restored as the canonical
`converters/python/otf2_convert.py` (shipped in every new container image); the incorrect
GIL-bound attempt is kept for reference only as `converters/python/otf2_convert_parallel_incorrect.py`
and is not used by any build/run script. `_save`'s numbers match the restored canonical code.

## Replicating on Frontier

1. Run the benchmark as usual (outputs land in ignored `out/`).
2. Edit `SRC_RUN` + `SYSTEM = "frontier"` at the top of `tools/build_bench_analysis_data.py`, run
   it → writes `analysis-data/frontier/<run>/` with a **sanitized** `config.json`.
3. `git status` + eyeball the staged `config.json` (confirm `account`/mail show `<redacted>`),
   check size, commit.
