#!/usr/bin/env python3
# Copyright Hewlett Packard Enterprise Development LP.
"""
otf2_convert_parallel_incorrect.py -- **NOT the canonical Python converter.**

*** KEPT FOR REFERENCE / HISTORICAL COMPARISON ONLY -- NOT USED BY THE CONTAINER BUILD ***

WHY THIS IS INCORRECT: this version uses a `ThreadPoolExecutor` to "parallelize" per-location
reads and per-file writes, but the pure-Python `otf2` reader + CallGraph/dict manipulation is
almost entirely CPU-bound Python bytecode. CPython's GIL serializes bytecode execution across
threads, so this does NOT actually run in parallel -- it's still fundamentally single-threaded
work, just spread across worker threads with added scheduling/context-switch overhead and no
real speedup. (Threads only help here for the brief moments a thread releases the GIL, e.g. in C
extensions like pyarrow's Parquet writer -- not for the OTF2 parsing itself.)

The CANONICAL Python baseline is `otf2_convert.py` (restored to its original single-threaded,
streaming design) -- that is what `Containerfile.bench` copies into new images and what
`run_one.sh` invokes. This file is kept only so the "GIL-bound, not actually parallel" attempt
can still be inspected/benchmarked for comparison; it is NOT referenced by any build or run
script.

Emits, per the FastOTF2Converter schema:
  <outputDir>/<Group>_<Thread>_callgraph.<ext>
    columns: Thread, Group, Depth, Name, Start Time, End Time, Duration
  <outputDir>/<Group>_metrics.<ext>
    columns: Group, Metric Name, Time, Value

CSV     -> times in seconds (float).
Parquet -> times in nanoseconds (int64).

Usage:
  otf2_convert_parallel_incorrect.py <trace.otf2> [--format CSV|PARQUET] [--outputDir DIR]
                  [--keep-dups] [--jobs N]
"""
import argparse
import os
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

import otf2


# ---------------------------------------------------------------------------
# Call-graph model (ported from the original convert.py; the conversion subset).
# ---------------------------------------------------------------------------
class Interval(NamedTuple):
    start: float
    end: Optional[float] = None
    depth: float = 0
    name: Optional[str] = None

    def has_overlap(self, other: "Interval") -> bool:
        my_end = self.end if self.end is not None else float("inf")
        other_end = other.end if other.end is not None else float("inf")
        return self.start < other_end and other.start < my_end

    def clip(self, start: float, end: float) -> "Interval":
        new_start = max(self.start, start)
        new_end = min(self.end, end) if self.end is not None else end
        return self._replace(start=new_start, end=new_end)


class CallGraph:
    """A per-location timeline of enter/leave intervals (like the original)."""

    def __init__(self):
        self.finished_intervals: List[Interval] = []
        self.live_intervals: List[Interval] = []

    def enter(self, start: float, name: Optional[str] = None) -> None:
        depth = len(self.live_intervals) + 1
        self.live_intervals.append(Interval(start=start, depth=depth, name=name))

    def leave(self, end: float) -> None:
        if not self.live_intervals:
            raise ValueError("No active intervals to leave.")
        interval = self.live_intervals.pop()
        self.finished_intervals.append(interval._replace(end=end))

    def get_intervals_between(self, start: float, end: float) -> List[Interval]:
        if start > end:
            raise ValueError("Start time must be less than or equal to end time.")
        window = Interval(start, end)
        overlapping = (
            iv for iv in (self.finished_intervals + self.live_intervals)
            if iv.has_overlap(window)
        )
        clipped = (iv.clip(start, end) for iv in overlapping)
        return sorted(
            clipped, key=lambda x: (x.start, x.end if x.end is not None else float("inf"))
        )


# ---------------------------------------------------------------------------
# Time base (module globals, as in the original: set once, read by worker threads).
# ---------------------------------------------------------------------------
start_time: Optional[int] = None      # ticks of the ProgramBegin event
timer_resolution: Optional[int] = None  # ticks per second


def timestamp_to_seconds(timestamp: Optional[int]) -> float:
    if timestamp is None or start_time is None or timer_resolution is None:
        return 0.0
    return (timestamp - start_time) / timer_resolution


def sanitize(name: str) -> str:
    """Match the C/Chapel converters: spaces in thread names become underscores."""
    return name.replace(" ", "_")


# Duplicate (group, name) locations are disambiguated by handing out successive
# indices, exactly as the original converter did.
MATCHES: Dict[Tuple[str, str], int] = {}
MATCH_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Per-location read task: opens its OWN reader and reads only this location's
# events, building a CallGraph and a dedup'd per-metric sample list.
# ---------------------------------------------------------------------------
def process_location(location, trace_path: str, dedup: bool):
    call_graph = CallGraph()
    local_metrics: Dict[str, List[Tuple[float, Any]]] = {}

    with otf2.reader.open(trace_path) as reader:
        matches = [
            loc for loc in reader.definitions.locations
            if loc.name == location.name and loc.group.name == location.group.name
        ]
        if not matches:
            return None
        if len(matches) > 1:
            with MATCH_LOCK:
                key = (location.group.name, location.name)
                idx = MATCHES.get(key, 0)
                MATCHES[key] = idx + 1
            selected = matches[min(idx, len(matches) - 1)]
        else:
            selected = matches[0]

        for _, event in reader.events([selected]):
            current_time = timestamp_to_seconds(event.time)
            if isinstance(event, otf2.events.Metric):
                metric_name = event.member.name
                bucket = local_metrics.setdefault(metric_name, [])
                if (not dedup) or (not bucket) or (bucket[-1][1] != event.value):
                    bucket.append((current_time, event.value))
            elif isinstance(event, otf2.events.Enter):
                call_graph.enter(current_time, name=event.region.name)
            elif isinstance(event, otf2.events.Leave):
                try:
                    call_graph.leave(current_time)
                except ValueError:
                    continue

    return (location.group.name, location.name, call_graph, local_metrics)


# ---------------------------------------------------------------------------
# Writers -- CSV (as in the original) and Parquet (additive). Each writes ONE
# output file in a single pass (no streaming/batching).
# ---------------------------------------------------------------------------
def _to_nanos(seconds: float) -> int:
    return int(round(seconds * 1e9))


def callgraph_to_csv(call_graph: CallGraph, group: str, thread: str, filename: str) -> None:
    with open(filename, "w") as f:
        f.write("Thread,Group,Depth,Name,Start Time,End Time,Duration\n")
        for interval in call_graph.get_intervals_between(float("-inf"), float("inf")):
            start = interval.start
            end = interval.end if interval.end is not None else float("inf")
            duration = end - start
            name = interval.name if interval.name else "Unknown"
            depth = interval.depth if interval.depth else 0
            f.write(f'{thread},{group},{depth},"{name}",{start},{end},{duration}\n')


def metrics_to_csv(group: str, thread_metrics: Dict[str, List[Tuple[float, Any]]],
                   filename: str) -> None:
    with open(filename, "w") as f:
        f.write("Group,Metric Name,Time,Value\n")
        for metric_name, values in thread_metrics.items():
            for t, value in values:
                f.write(f"{group},{metric_name},{t},{value}\n")


def callgraph_to_parquet(call_graph: CallGraph, group: str, thread: str, filename: str) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    threads, groups, depths, names, starts, ends, durations = [], [], [], [], [], [], []
    for interval in call_graph.get_intervals_between(float("-inf"), float("inf")):
        start = interval.start
        end = interval.end if interval.end is not None else start
        name = interval.name if interval.name else "Unknown"
        depth = int(interval.depth) if interval.depth else 0
        s_ns, e_ns = _to_nanos(start), _to_nanos(end)
        threads.append(thread); groups.append(group); depths.append(depth)
        names.append(name); starts.append(s_ns); ends.append(e_ns)
        durations.append(e_ns - s_ns)

    table = pa.table(
        {
            "Thread": pa.array(threads, pa.string()),
            "Group": pa.array(groups, pa.string()),
            "Depth": pa.array(depths, pa.int32()),
            "Name": pa.array(names, pa.string()),
            "Start Time": pa.array(starts, pa.int64()),
            "End Time": pa.array(ends, pa.int64()),
            "Duration": pa.array(durations, pa.int64()),
        }
    )
    pq.write_table(table, filename, compression="snappy")


def metrics_to_parquet(group: str, thread_metrics: Dict[str, List[Tuple[float, Any]]],
                       filename: str) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    groups, names, times, values = [], [], [], []
    for metric_name, samples in thread_metrics.items():
        for t, value in samples:
            groups.append(group); names.append(metric_name)
            times.append(_to_nanos(t)); values.append(float(value))

    table = pa.table(
        {
            "Group": pa.array(groups, pa.string()),
            "Metric Name": pa.array(names, pa.string()),
            "Time": pa.array(times, pa.int64()),
            "Value": pa.array(values, pa.float64()),
        }
    )
    pq.write_table(table, filename, compression="snappy")


# ---------------------------------------------------------------------------
# Driver: parallel per-location read, then parallel per-file write.
# ---------------------------------------------------------------------------
def convert(trace_path: str, output_dir: str, fmt: str, dedup: bool, jobs: int):
    global start_time, timer_resolution

    fmt = fmt.upper()
    parquet = fmt == "PARQUET"
    ext = "parquet" if parquet else "csv"
    os.makedirs(output_dir, exist_ok=True)

    print(f"Converting trace at {trace_path} to {fmt} using {jobs} workers...", flush=True)
    baseline = time.time()

    call_graphs: Dict[str, Dict[str, CallGraph]] = {}
    metrics: Dict[str, Dict[str, List[Tuple[float, Any]]]] = {}

    pool = ThreadPoolExecutor(max_workers=jobs)

    # --- Discover locations + establish the time base from one reader. ---
    with otf2.reader.open(trace_path) as reader:
        timer_resolution = reader.timer_resolution
        for _, event in reader.events:
            if isinstance(event, otf2.events.ProgramBegin):
                start_time = event.time
                break
        locations = sorted(
            reader.definitions.locations, key=lambda loc: (loc.group.name, loc.name)
        )

    # --- Parallel READ: one task per location, each with its own reader. ---
    futures = [pool.submit(process_location, loc, trace_path, dedup) for loc in locations]
    for future in as_completed(futures):
        result = future.result()
        if not isinstance(result, tuple):
            continue
        group, thread, call_graph, local_metrics = result
        call_graphs.setdefault(group, {})[thread] = call_graph
        group_metrics = metrics.setdefault(group, {})
        for metric_name, values in local_metrics.items():
            group_metrics.setdefault(metric_name, []).extend(values)

    print(f"Reading completed in {time.time() - baseline:.2f} seconds.", flush=True)
    write_start = time.time()

    # --- Parallel WRITE: one task per callgraph file and one per metrics file. ---
    write_callgraph = callgraph_to_parquet if parquet else callgraph_to_csv
    write_metrics = metrics_to_parquet if parquet else metrics_to_csv

    futures = []
    for group, threads in call_graphs.items():
        for thread, call_graph in threads.items():
            filename = os.path.join(output_dir, f"{group}_{sanitize(thread)}_callgraph.{ext}")
            futures.append(pool.submit(write_callgraph, call_graph, group, thread, filename))
    for group, thread_metrics in metrics.items():
        filename = os.path.join(output_dir, f"{group}_metrics.{ext}")
        futures.append(pool.submit(write_metrics, group, thread_metrics, filename))
    for future in as_completed(futures):
        future.result()

    pool.shutdown(wait=True)

    print(f"Writing completed in {time.time() - write_start:.2f} seconds.", flush=True)
    total = time.time() - baseline
    print(f"{fmt} conversion completed in {total:.3f} seconds ({total / 60:.2f} minutes).",
          flush=True)


def main():
    p = argparse.ArgumentParser(description="Convert an OTF2 trace to CSV or Parquet (Python).")
    p.add_argument("trace", help="Path to the OTF2 trace archive (traces.otf2)")
    p.add_argument("--format", default="CSV", choices=["CSV", "PARQUET", "csv", "parquet"],
                   help="Output format (default: CSV)")
    p.add_argument("--outputDir", default="./", help="Directory for output files")
    p.add_argument("--keep-dups", action="store_true",
                   help="Do not skip consecutive duplicate metric values")
    p.add_argument("--jobs", type=int, default=64,
                   help="Number of worker threads for reading/writing (default: 64)")
    args = p.parse_args()

    convert(
        trace_path=args.trace,
        output_dir=args.outputDir,
        fmt=args.format,
        dedup=not args.keep_dups,
        jobs=max(1, args.jobs),
    )


if __name__ == "__main__":
    main()
