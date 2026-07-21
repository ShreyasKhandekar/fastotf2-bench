#!/usr/bin/env python3
# Copyright Hewlett Packard Enterprise Development LP.
"""
otf2_convert.py -- convert an OTF2 trace to CSV or Parquet using the pure-Python
`otf2` reader. This is the CANONICAL Python baseline for the fastotf2-bench converter
comparison -- single-threaded, streaming -- copied into every new container image by
`Containerfile.bench` and invoked by `benchmark/run_one.sh`.

RESTORED 2026-07-21: a "parallel" variant briefly replaced this file (ThreadPoolExecutor,
one task per location), but it is NOT actually parallel -- the pure-Python `otf2` reader is
CPU-bound Python bytecode, and CPython's GIL serializes bytecode execution across threads, so
that version got thread-scheduling overhead with no real speedup. That incorrect variant is
kept for reference ONLY as `otf2_convert_parallel_incorrect.py` (not used by any build/run
script); THIS file is the one that ships.

Emits, per the FastOTF2Converter schema:
  <outputDir>/<Group>_<Thread>_callgraph.<ext>
    columns: Thread, Group, Depth, Name, Start Time, End Time, Duration
  <outputDir>/<Group>_metrics.<ext>
    columns: Group, Metric Name, Time, Value

CSV  -> times in seconds (float).
Parquet -> times in nanoseconds (int64), matching the Chapel converter.

Events are streamed and written in batches so memory stays bounded even on very
large traces.

Usage:
  otf2_convert.py <trace.otf2> [--format CSV|PARQUET] [--outputDir DIR]
                  [--keep-dups] [--read-only]
"""
import argparse
import os
import time

import otf2


FLUSH_ROWS = 100_000  # rows buffered per output file before a Parquet flush
PROGRESS_EVERY = 2_000_000  # print a progress heartbeat every N events processed


def sanitize(name: str) -> str:
    """Match the C/Chapel converters: spaces in thread names become underscores."""
    return name.replace(" ", "_")


def elapsed_str(seconds: float) -> str:
    """Format seconds as e.g. '1h23m45s' for progress heartbeats."""
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h{m:02d}m{s:02d}s"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"


class CsvWriter:
    """Streaming CSV writer for one output file."""

    def __init__(self, path: str, header: str):
        self._f = open(path, "w")
        self._f.write(header + "\n")

    def write_callgraph(self, thread, group, depth, name, start, end, duration):
        # Quote the region name; it may contain commas.
        self._f.write(
            f'{thread},{group},{depth},"{name}",{start:.9f},{end:.9f},{duration:.9f}\n'
        )

    def write_metric(self, group, metric_name, t, value):
        self._f.write(f'{group},"{metric_name}",{t:.9f},{value}\n')

    def close(self):
        self._f.close()


class ParquetWriter:
    """Batched Parquet writer for one output file (bounded memory)."""

    def __init__(self, path: str, schema):
        import pyarrow.parquet as pq

        self._pq = pq
        self._schema = schema
        self._writer = pq.ParquetWriter(path, schema, compression="snappy")
        self._cols = [[] for _ in schema.names]
        self._n = 0

    def _append(self, *values):
        for i, v in enumerate(values):
            self._cols[i].append(v)
        self._n += 1
        if self._n >= FLUSH_ROWS:
            self._flush()

    def _flush(self):
        if self._n == 0:
            return
        import pyarrow as pa

        batch = pa.record_batch(
            [pa.array(col, type=self._schema.field(i).type) for i, col in enumerate(self._cols)],
            schema=self._schema,
        )
        self._writer.write_batch(batch)
        self._cols = [[] for _ in self._schema.names]
        self._n = 0

    def close(self):
        self._flush()
        self._writer.close()


def _build_parquet_schemas():
    import pyarrow as pa

    callgraph = pa.schema([
        ("Thread", pa.string()),
        ("Group", pa.string()),
        ("Depth", pa.int32()),
        ("Name", pa.string()),
        ("Start Time", pa.int64()),
        ("End Time", pa.int64()),
        ("Duration", pa.int64()),
    ])
    metrics = pa.schema([
        ("Group", pa.string()),
        ("Metric Name", pa.string()),
        ("Time", pa.int64()),
        ("Value", pa.float64()),
    ])
    return callgraph, metrics


def convert(trace_path: str, output_dir: str, fmt: str, dedup: bool, read_only: bool):
    fmt = fmt.upper()
    parquet = fmt == "PARQUET"
    if not read_only:
        os.makedirs(output_dir, exist_ok=True)

    if parquet:
        cg_schema, met_schema = _build_parquet_schemas()

    callgraph_writers = {}   # (group, thread) -> writer
    metric_writers = {}       # group -> writer
    stacks = {}               # location id -> list of (start_ticks, region_name, depth)
    last_metric = {}          # (group, metric_name) -> last value (dedup)

    t0 = time.time()
    with otf2.reader.open(trace_path) as trace:
        clock = trace.definitions.clock_properties
        resolution = float(clock.timer_resolution)
        offset = clock.global_offset

        def to_seconds(ticks):
            return (ticks - offset) / resolution if resolution else 0.0

        def to_nanos(ticks):
            return int(round((ticks - offset) * 1e9 / resolution)) if resolution else 0

        t_open = time.time()
        print(f"Opened trace in {t_open - t0:.2f} s", flush=True)

        n_events = 0
        t_last_progress = t_open
        for location, event in trace.events:
            n_events += 1
            if n_events % PROGRESS_EVERY == 0:
                now = time.time()
                rate = PROGRESS_EVERY / (now - t_last_progress) if now > t_last_progress else 0.0
                print(
                    f"  ... {n_events:,} events processed "
                    f"({elapsed_str(now - t0)} elapsed, {rate:,.0f} events/s, "
                    f"{len(callgraph_writers)} callgraph files open)",
                    flush=True,
                )
                t_last_progress = now

            loc_id = location._ref if hasattr(location, "_ref") else id(location)
            thread = sanitize(location.name)
            group = location.group.name if getattr(location, "group", None) else "Unknown"

            if isinstance(event, otf2.events.Enter):
                stk = stacks.setdefault(loc_id, [])
                stk.append((event.time, event.region.name, len(stk)))

            elif isinstance(event, otf2.events.Leave):
                stk = stacks.get(loc_id)
                if not stk:
                    continue
                start_ticks, region_name, depth = stk.pop()
                if read_only:
                    continue
                key = (group, thread)
                w = callgraph_writers.get(key)
                if w is None:
                    fname = os.path.join(output_dir, f"{group}_{thread}_callgraph")
                    if parquet:
                        w = ParquetWriter(fname + ".parquet", cg_schema)
                    else:
                        w = CsvWriter(fname + ".csv",
                                      "Thread,Group,Depth,Name,Start Time,End Time,Duration")
                    callgraph_writers[key] = w
                if parquet:
                    s = to_nanos(start_ticks); e = to_nanos(event.time)
                    w._append(thread, group, depth, region_name, s, e, e - s)
                else:
                    s = to_seconds(start_ticks); e = to_seconds(event.time)
                    w.write_callgraph(thread, group, depth, region_name, s, e, e - s)

            elif isinstance(event, otf2.events.Metric):
                member = getattr(event, "member", None)
                if member is None:
                    continue
                metric_name = member.name
                value = event.value
                if dedup:
                    dk = (group, metric_name)
                    if last_metric.get(dk) == value:
                        continue
                    last_metric[dk] = value
                if read_only:
                    continue
                w = metric_writers.get(group)
                if w is None:
                    fname = os.path.join(output_dir, f"{group}_metrics")
                    if parquet:
                        w = ParquetWriter(fname + ".parquet", met_schema)
                    else:
                        w = CsvWriter(fname + ".csv", "Group,Metric Name,Time,Value")
                    metric_writers[group] = w
                if parquet:
                    w._append(group, metric_name, to_nanos(event.time), float(value))
                else:
                    w.write_metric(group, metric_name, to_seconds(event.time), value)

    for w in callgraph_writers.values():
        w.close()
    for w in metric_writers.values():
        w.close()

    elapsed = time.time() - t0
    action = "Read" if read_only else f"{fmt} conversion"
    print(f"{action} completed in {elapsed:.3f} seconds ({n_events:,} events).", flush=True)


def main():
    p = argparse.ArgumentParser(description="Convert an OTF2 trace to CSV or Parquet (Python).")
    p.add_argument("trace", help="Path to the OTF2 trace archive (traces.otf2)")
    p.add_argument("--format", default="CSV", choices=["CSV", "PARQUET", "csv", "parquet"],
                   help="Output format (default: CSV)")
    p.add_argument("--outputDir", default="./", help="Directory for output files")
    p.add_argument("--keep-dups", action="store_true",
                   help="Do not skip consecutive duplicate metric values")
    p.add_argument("--read-only", action="store_true",
                   help="Read/parse the trace but write no output")
    args = p.parse_args()

    convert(
        trace_path=args.trace,
        output_dir=args.outputDir,
        fmt=args.format,
        dedup=not args.keep_dups,
        read_only=args.read_only,
    )


if __name__ == "__main__":
    main()
