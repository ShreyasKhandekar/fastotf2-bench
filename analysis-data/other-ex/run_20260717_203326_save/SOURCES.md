# SOURCES — fastotf2-benchmark analysis data (other-ex)

- Source run: `run_20260717_203326_save` (the canonical `_save` run; = ampere's `python_run`).
- Copied: results.csv, trace_sizes.json, plots/ (all non-sensitive).
- config.json is SANITIZED (account/mail/user redacted via build_bench_analysis_data.py).
- NOTE (code provenance): this run's PYTHON numbers are from the SERIAL python converter,
  which was later replaced in the repo by a 'parallel' version that is GIL-bound (not
  actually parallel). Restore the serial version as canonical; `_save` == that serial code.
- Dropped: slurm_logs/, run_logs/, scratch/, manifest.csv (bulky and/or sensitive).
- Re-run C analysis: set the notebook's ANALYZE_RUN to this folder.
