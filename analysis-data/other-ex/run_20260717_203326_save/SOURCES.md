# SOURCES — fastotf2-benchmark analysis data (other-ex)

- Source run: `run_20260717_203326_save` (the canonical `_save` run; = ampere's `python_run`).
- Copied: results.csv, trace_sizes.json, plots/ (all non-sensitive).
- config.json is SANITIZED (account/mail/user redacted via build_bench_analysis_data.py).
- NOTE (code provenance, RESOLVED): this run's PYTHON numbers are from the SERIAL python
  converter. It was briefly replaced in the repo by a 'parallel' version that turned out
  to be GIL-bound (not actually parallel) -- fixed: the serial version is restored as the
  canonical converters/python/otf2_convert.py; the incorrect attempt is kept for reference
  only as otf2_convert_parallel_incorrect.py. `_save` matches the restored canonical code.
- Dropped: slurm_logs/, run_logs/, scratch/, manifest.csv (bulky and/or sensitive).
- Re-run C analysis: set the notebook's ANALYZE_RUN to this folder.
