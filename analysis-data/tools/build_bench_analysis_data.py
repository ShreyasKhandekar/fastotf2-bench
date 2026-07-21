#!/usr/bin/env python3
"""Build C's (fastotf2-benchmark) analysis-data: the small, clean, git-safe subset needed to
re-run the benchmark notebook's analysis (§4) on another machine.

C's analysis is self-contained from `results.csv` (the notebook says so). We copy:
  results.csv, trace_sizes.json, plots/   (all non-sensitive)
and a SANITIZED config.json (provenance).

SENSITIVITY / the "account on Frontier" problem
-----------------------------------------------
`config.json` has a `slurm` sub-dict with `account` (null on this system, but a REAL account on
Frontier) and `extra_args` (which on Frontier carries `--account=...` / `--mail-user=...`).
Filepaths/system label are OK to keep; account/email are NOT. `sanitize_json()` recursively
redacts sensitive KEYS (account/mail/user/secret/token/password) and sensitive VALUES
(`--account=`/`--mail-user=` style flags and anything that looks like an email), so the uploaded
config is safe regardless of which system produced it. On this system it's effectively a no-op.

Re-run on another system by editing SRC_RUN / SYSTEM below (e.g. SYSTEM="frontier").
"""
import json
import re
import shutil
import sys
from pathlib import Path

# ---- Configure (edit when replicating on another system) ----
REPO = Path(__file__).resolve().parents[2]        # fastotf2-bench/
SRC_RUN = REPO / "out" / "run_20260717_203326_save"
SYSTEM = "other-ex"                               # neutral label; "frontier" on Frontier
OUT = REPO / "analysis-data" / SYSTEM / SRC_RUN.name

# Redact KEYS whose name looks secret, and VALUES that look like account/mail flags or emails.
_SENS_KEY = re.compile(r"(account|mail|secret|token|password|passwd)", re.I)
_SENS_VAL = re.compile(r"(--?(account|mail[-_]?user|uid)\b|[\w.+-]+@[\w.-]+)", re.I)
REDACT = "<redacted>"


def sanitize_json(obj):
    """Recursively redact account/mail/user-ish keys and flag/email-style values. Keeps shape
    (so it's obvious a field existed and was scrubbed) and everything non-sensitive intact."""
    if isinstance(obj, dict):
        return {k: (REDACT if _SENS_KEY.search(k) else sanitize_json(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize_json(x) for x in obj]
    if isinstance(obj, str) and _SENS_VAL.search(obj):
        return REDACT
    return obj


def main():
    if not SRC_RUN.is_dir():
        sys.exit(f"ERROR: source run not found: {SRC_RUN}")
    OUT.mkdir(parents=True, exist_ok=True)

    # Plain copies (verified non-sensitive: numeric timings, size cache, figures).
    for name in ("results.csv", "trace_sizes.json"):
        src = SRC_RUN / name
        if src.exists():
            shutil.copy2(src, OUT / name)
        else:
            print(f"WARNING: {name} missing in {SRC_RUN}")
    if (SRC_RUN / "plots").is_dir():
        shutil.copytree(SRC_RUN / "plots", OUT / "plots", dirs_exist_ok=True)

    # Sanitized provenance config.
    cfg_src = SRC_RUN / "config.json"
    if cfg_src.exists():
        clean = sanitize_json(json.loads(cfg_src.read_text()))
        (OUT / "config.json").write_text(json.dumps(clean, indent=2))
        # Report what got scrubbed, for peace of mind.
        raw = cfg_src.read_text()
        print("config.json sanitized. slurm block now:",
              json.dumps(clean.get("slurm"), indent=1) if isinstance(clean, dict) else clean)

    (OUT / "SOURCES.md").write_text(
        f"# SOURCES — fastotf2-benchmark analysis data ({SYSTEM})\n\n"
        f"- Source run: `{SRC_RUN.name}` (the canonical `_save` run; = ampere's `python_run`).\n"
        f"- Copied: results.csv, trace_sizes.json, plots/ (all non-sensitive).\n"
        f"- config.json is SANITIZED (account/mail/user redacted via build_bench_analysis_data.py).\n"
        f"- NOTE (code provenance, RESOLVED): this run's PYTHON numbers are from the SERIAL python\n"
        f"  converter. It was briefly replaced in the repo by a 'parallel' version that turned out\n"
        f"  to be GIL-bound (not actually parallel) -- fixed: the serial version is restored as the\n"
        f"  canonical converters/python/otf2_convert.py; the incorrect attempt is kept for reference\n"
        f"  only as otf2_convert_parallel_incorrect.py. `_save` matches the restored canonical code.\n"
        f"- Dropped: slurm_logs/, run_logs/, scratch/, manifest.csv (bulky and/or sensitive).\n"
        f"- Re-run C analysis: set the notebook's ANALYZE_RUN to this folder.\n")

    print(f"Wrote {OUT}")
    for f in sorted(OUT.rglob("*")):
        if f.is_file():
            print("  ", f.relative_to(OUT), f"({f.stat().st_size} B)")


if __name__ == "__main__":
    main()
